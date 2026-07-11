# main.py - VERSION FIXED (CHATBOT + TIMESTAMP + OCR + LOCAL KNOWLEDGE)
from datetime import datetime, timedelta
import os
import re
import io
import json
import numpy as np
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
import asyncio
import time
from contextlib import asynccontextmanager
import httpx
import logging

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

import pytesseract
import cv2
import requests
import tempfile

from app import ml          # Model OTDR (LightGBM)
from app import ml_sor      # Model SOR (Random Forest) - BARU
from app.database import Base, engine, get_db, AsyncSessionLocal
from app.models import User, OtdrResult, DashboardResult
from app.schemas import (
    UserRegister, UserLogin, TokenResponse, UserOut, ManualClassifyRequest,
    DashboardResultResponse,
)
from app.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, get_optional_user, get_current_admin
)
from app.parseotdr import parse_otdr_table, extract_prx

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


# Tesseract path - auto detect (keep for fallback)
if os.name == 'nt':  # Windows
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:  # Mac/Linux
    possible_paths = [
        '/opt/homebrew/bin/tesseract',
        '/usr/local/bin/tesseract',
        '/usr/bin/tesseract'
    ]
    for path in possible_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            break

# Google Sheets CSV export URL
SHEET_ID = "1dN2Q7zrp_M2RZ8o0-GPjYyL4yfZPo0KHjAKhEx8Qudo"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@optim.com")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

last_dashboard_slide_index = 0
last_dashboard_slide_data = None

# ── Shared slide state: satu posisi untuk semua user/device ──
shared_slide_index = 0
slide_alert_sent_ids: set = set()  # Track ID yang sudah dikirim alert, cegah duplikat

def send_telegram_alert(classification: str, status: str, loss: list, rl: list, prx, distances: list = None, timestamp = None):
    """Mengirim pesan notifikasi gangguan ke Telegram Teknisi dengan format detail baru"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("[TELEGRAM] Notifikasi dibatalkan karena token/chat_id belum dikonfigurasi di .env")
        return

    status_lower = status.lower()
    if status_lower not in ["warning", "critical"]:
        logger.info(f"[TELEGRAM] Status '{status}' Normal, alert tidak dikirim.")
        return

    # Format values safely — None tetap None untuk pengecekan '---'
    prx_val = round(float(prx), 2) if prx is not None else 0.0

    # Konversi loss dan return: None → None (bukan 0.0), agar bisa ditampilkan '---'
    def to_val(v):
        if v is None: return None
        try:
            f = float(v)
            return f if f == f else None  # filter NaN
        except: return None

    raw_loss = [to_val(l) for l in loss]
    raw_rl   = [to_val(r) for r in rl]

    while len(raw_loss) < 4: raw_loss.append(None)
    while len(raw_rl)   < 4: raw_rl.append(None)

    # Format untuk tampilan: None atau 0.0 → '---'
    def fmt_loss(v):
        return '---' if v is None or v == 0.0 else f'{v:.2f} dB'
    def fmt_return(v):
        return '---' if v is None or v == 0.0 else f'{v:.2f} dB'

    if not distances:
        distances = [1.004, 2.006, 3.010, 4.014]

    safe_dist = []
    for i in range(4):
        if i < len(distances) and distances[i] is not None:
            try: safe_dist.append(float(distances[i]))
            except: safe_dist.append(float(i + 1))
        else:
            safe_dist.append(float(i + 1))

    classification_lower = classification.lower() if classification else ""
    is_fiber_cut = "cut" in classification_lower or "putus" in classification_lower

    if is_fiber_cut:
        # Lokasi putus = KM pertama yang loss-nya None atau 0
        cut_idx = next((i for i, v in enumerate(raw_loss) if v is None or v == 0.0), 3)
        km_loc    = cut_idx + 1
        jarak_loc = round(safe_dist[cut_idx], 3)
        redaman_loc = None  # tidak terdefinisi di titik putus
    else:
        # Lokasi gangguan = KM dengan loss REAL terbesar
        # Skip None dan 0 — hanya pertimbangkan nilai yang benar-benar terukur
        valid_losses = [
            (i, v) for i, v in enumerate(raw_loss)
            if v is not None and v > 0
        ]
        if valid_losses:
            max_loss_idx, max_loss_val = max(valid_losses, key=lambda x: x[1])
        else:
            max_loss_idx, max_loss_val = 0, 0.0
        km_loc      = max_loss_idx + 1
        jarak_loc   = round(safe_dist[max_loss_idx], 3)
        redaman_loc = round(max_loss_val, 2)
    
    if timestamp:
        if isinstance(timestamp, str):
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                local_time = dt + timedelta(hours=7)
                time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                time_str = timestamp.replace("T", " ")[:19]
        else:
            local_time = timestamp + timedelta(hours=7)
            time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        local_time = datetime.utcnow() + timedelta(hours=7)
        time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
        
    status_cap = str(status).capitalize()

    message = (
        f"🚨 <b>GANGGUAN TERDETEKSI!</b> 🚨\n\n"
        f"<b>Jenis Gangguan:</b> {classification}\n"
        f"<b>Tingkat Bahaya:</b> {status_cap}\n\n"
        f"<b>Parameter Pengukuran:</b>\n"
        f"• <b>Daya (Prx):</b> {prx_val} dBm\n\n"
        f"<b>Detail Redaman &amp; Pantulan:</b>\n"
        f"• <b>KM 1:</b> Loss {fmt_loss(raw_loss[0])} | Return {fmt_return(raw_rl[0])}\n"
        f"• <b>KM 2:</b> Loss {fmt_loss(raw_loss[1])} | Return {fmt_return(raw_rl[1])}\n"
        f"• <b>KM 3:</b> Loss {fmt_loss(raw_loss[2])} | Return {fmt_return(raw_rl[2])}\n"
        f"• <b>KM 4:</b> Loss {fmt_loss(raw_loss[3])} | Return {fmt_return(raw_rl[3])}\n\n"
        f"<b>Lokasi Gangguan:</b> KM {km_loc} (Jarak: {jarak_loc:.3f} km, Redaman: {'---' if redaman_loc is None else f'{redaman_loc:.2f} dB'})\n"
        f"<b>Waktu:</b> {time_str}"
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chat_ids = [cid.strip() for cid in str(TELEGRAM_CHAT_ID).split(",") if cid.strip()]
    
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"[TELEGRAM] Alert '{status}' berhasil dikirim ke ID: {chat_id}.")
            else:
                logger.error(f"[TELEGRAM] Gagal mengirim alert ke ID {chat_id}: {response.text}")
        except Exception as e:
            logger.error(f"[TELEGRAM] Error koneksi ke ID {chat_id}: {e}")

# ═══════════════════════════════════════════════════════════════════
# FUNGSI MAPPING KOLOM
# ═══════════════════════════════════════════════════════════════════

REQUIRED_FEATURES = [
    "Prx (dBm)", "Distance 1", "Distance 2", "Distance 3", "Distance 4",
    "Loss 1", "Loss 2", "Loss 3", "Total-L 1", "Total-L 2", "Total-L 3", "Total-L 4",
    "Avg-L 1", "Avg-L 2", "Avg-L 3", "Avg-L 4", "Avg-Total",
    "Return 1", "Return 2", "Return 3", "Return 4"
]

def create_column_mapping(df_columns: list) -> dict:
    col_lower = {c.lower().strip(): c for c in df_columns}
    
    keyword_mapping = {
        'Prx (dBm)': ['prx (dbm)', 'prx', 'rx power', 'received power', 'prx(dbm)', 'prx_dbm'],
        'Distance 1': ['distance 1', 'dist 1', 'jarak 1', 'distance_1'],
        'Distance 2': ['distance 2', 'dist 2', 'jarak 2', 'distance_2'],
        'Distance 3': ['distance 3', 'dist 3', 'jarak 3', 'distance_3'],
        'Distance 4': ['distance 4', 'dist 4', 'jarak 4', 'distance_4'],
        'Loss 1': ['loss 1', 'redaman 1', 'attenuation 1', 'loss_1'],
        'Loss 2': ['loss 2', 'redaman 2', 'attenuation 2', 'loss_2'],
        'Loss 3': ['loss 3', 'redaman 3', 'attenuation 3', 'loss_3'],
        'Total-L 1': ['total-l 1', 'total loss 1', 'total_l_1'],
        'Total-L 2': ['total-l 2', 'total loss 2', 'total_l_2'],
        'Total-L 3': ['total-l 3', 'total loss 3', 'total_l_3'],
        'Total-L 4': ['total-l 4', 'total loss 4', 'total_l_4'],
        'Avg-L 1': ['avg-l 1', 'average loss 1', 'avg_l_1'],
        'Avg-L 2': ['avg-l 2', 'average loss 2', 'avg_l_2'],
        'Avg-L 3': ['avg-l 3', 'average loss 3', 'avg_l_3'],
        'Avg-L 4': ['avg-l 4', 'average loss 4', 'avg_l_4'],
        'Avg-Total': ['avg-total', 'total average', 'avg_total'],
        'Return 1': ['return 1', 'orl 1', 'return loss 1', 'return_1'],
        'Return 2': ['return 2', 'orl 2', 'return loss 2', 'return_2'],
        'Return 3': ['return 3', 'orl 3', 'return loss 3', 'return_3'],
        'Return 4': ['return 4', 'orl 4', 'return loss 4', 'return_4'],
    }
    
    mapping = {}
    for needed_field, keywords in keyword_mapping.items():
        for keyword in keywords:
            if keyword in col_lower:
                mapping[needed_field] = col_lower[keyword]
                logger.info(f"✅ Mapped '{needed_field}' → '{col_lower[keyword]}'")
                break
        if needed_field not in mapping:
            logger.warning(f"⚠️ Column '{needed_field}' not found in CSV")
    
    return mapping

def get_value_from_row(row, field_name: str, mapping: dict, default=0.0):
    if field_name in mapping:
        col_name = mapping[field_name]
        if col_name in row.index:
            val = row[col_name]
            try:
                if pd.isna(val) or val == '' or val == '-':
                    return default
                return float(val)
            except (ValueError, TypeError):
                return default
    return default

def calculate_missing_values(row, mapping: dict, distance: dict, total_l: dict) -> dict:
    result = {}
    
    for i in range(1, 5):
        avg_key = f'Avg-L {i}'
        total_key = f'Total-L {i}'
        dist_key = f'Distance {i}'
        
        avg_val = get_value_from_row(row, avg_key, mapping, 0)
        total_val = get_value_from_row(row, total_key, mapping, 0)
        dist_val = distance.get(dist_key, 0)
        
        if avg_val == 0 and total_val > 0 and dist_val > 0:
            avg_val = total_val / dist_val
        
        result[avg_key] = avg_val
    
    avg_total = get_value_from_row(row, 'Avg-Total', mapping, 0)
    if avg_total == 0:
        avg_total = result.get('Avg-L 4', 0)
    
    result['Avg-Total'] = avg_total
    
    # 🔥 HAPUS round() global — nilai OCR disimpan apa adanya, pembulatan hanya di display frontend
    return result

# ═══════════════════════════════════════════════════════════════════
# AUTO-DETECT FIBER CUT - TANPA MODE USER
# ═══════════════════════════════════════════════════════════════════

def detect_measurement_mode_from_rows(rows: list) -> tuple:
    """
    Deteksi mode dari hasil parsing OCR.
    Menggunakan loss_missing (posisi '---' di kolom Loss) sebagai sinyal utama,
    bukan nilai numerik yang bisa bergeser karena kolom kosong.
    Returns: (mode, cut_km)
    """
    if not rows or len(rows) < 2:
        return 'normal', -1

    # Cek loss_missing per KM1-KM3
    for i in range(min(3, len(rows))):
        if rows[i].get('loss_missing') and rows[i].get('total_l', 0) > 0:
            cut_km = i + 1
            return f'fiber_cut_km{cut_km}', cut_km

    # Fallback: cek loss is None dan total_l ada (untuk rows tanpa loss_missing flag)
    for i in range(min(3, len(rows))):
        if rows[i].get('loss') is None and rows[i].get('total_l', 0) > 0:
            cut_km = i + 1
            return f'fiber_cut_km{cut_km}', cut_km

    return 'normal', -1


def detect_manual_mode(payload: dict) -> tuple:
    """
    Deteksi mode dari input manual.
    Returns: (mode, cut_km)
    """
    def get_val(key, default=None):
        val = payload.get(key)
        if val is None or val == '':
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default
    
    l1 = get_val('loss_1')
    l2 = get_val('loss_2')
    l3 = get_val('loss_3')
    
    tl2 = get_val('total_l_2', 0)
    tl3 = get_val('total_l_3', 0)
    
    def has_loss(loss):
        return loss is not None and loss > 0
    
    has_l1 = has_loss(l1)
    has_l2 = has_loss(l2)
    has_l3 = has_loss(l3)
    
    # 🔥 Fiber Cut KM 2: loss_2 = None/0, loss_3 = None/0, tapi total_l_2 > 0
    if (not has_l2) and (not has_l3) and tl2 > 0:
        return 'fiber_cut_km2', 2
    
    # 🔥 Fiber Cut KM 3: loss_3 = None/0, tapi total_l_3 > 0
    if (not has_l3) and tl3 > 0:
        return 'fiber_cut_km3', 3
    
    # 🔥 Normal: semua loss ada
    if has_l1 and has_l2 and has_l3:
        return 'normal', -1
    
    # Fallback: cek distance
    d1 = get_val('distance_1', 0)
    d2 = get_val('distance_2', 0)
    d3 = get_val('distance_3', 0)
    
    if d1 > 0 and d2 > 0 and d3 > 0 and not has_l3:
        return 'fiber_cut_km3', 3
    
    return 'normal', -1


def normalize_for_model(mode: str, cut_km: int, parsed_data: dict) -> dict:
    """
    Normalisasi data ke format training model.

    Representasi Fiber Cut menggunakan NaN untuk semua feature yang tidak
    tersedia setelah titik putus — identik dengan dataset training.

    Fiber Cut KM2:
      Loss 2       = NaN  (End of Fiber, tidak terukur)
      Distance 3/4 = NaN  (tidak ada baris di tabel OTDR)
      Loss 3/4     = NaN
      Total-L 3/4  = NaN
      Avg-L 3/4    = NaN
      Return 3/4   = NaN

    Fiber Cut KM3:
      Loss 3       = NaN  (End of Fiber)
      Distance 4   = NaN
      Loss 4       = NaN
      Total-L 4    = NaN
      Avg-L 4      = NaN
      Return 4     = NaN
      (Total-L 3, Avg-L 3, Return 3 tetap dari OCR — masih terukur)
    """
    result = parsed_data.copy() if parsed_data else {}
    NAN = float('nan')

    if mode == 'fiber_cut_km2':
        result['loss_2']     = NAN
        result['distance_3'] = NAN
        result['loss_3']     = NAN
        result['total_l_3']  = NAN
        result['avg_l_3']    = NAN
        result['return_3']   = NAN
        result['distance_4'] = NAN
        result['loss_4']     = NAN
        result['total_l_4']  = NAN
        result['avg_l_4']    = NAN
        result['return_4']   = NAN
        logger.info("[NORMALIZE] Fiber Cut KM2: loss_2, semua KM3+KM4 → NaN")

    elif mode == 'fiber_cut_km3':
        result['loss_3']     = NAN
        result['distance_4'] = NAN
        result['loss_4']     = NAN
        result['total_l_4']  = NAN
        result['avg_l_4']    = NAN
        result['return_4']   = NAN
        logger.info("[NORMALIZE] Fiber Cut KM3: loss_3, semua KM4 → NaN")

    return result

# ═══════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE otdr_results ADD COLUMN telegram_alert_sent BOOLEAN DEFAULT FALSE;"))
            logger.info("✅ Database migration: added telegram_alert_sent column")
        except Exception as mig_err:
            logger.warning(f"⚠️ Migration check: {mig_err}")
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        admin = result.scalar_one_or_none()
        if not admin:
            admin_user = User(
                email=ADMIN_EMAIL,
                password=hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
                name="Administrator",
                is_admin=True,
                is_approved=True,
            )
            db.add(admin_user)
            await db.commit()
            logger.info(f"✅ Admin user created: {ADMIN_EMAIL}")
    
    logger.info("✅ Database tables ready")
    
    # 🔥 Auto-sync disabled - using manual sync only
    logger.info("⚠️ Auto-sync disabled - using manual sync only")
    
    yield
    
    logger.info("Shutting down...")


app = FastAPI(title="OptiM API", version="2.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "https://ashy-mushroom-0feb76700.7.azurestaticapps.net",
        # BUG FIX: wildcard "*" + allow_credentials=True adalah kombinasi ilegal per spec CORS.
        # Browser menolak SEMUA preflight OPTIONS request → "Failed to fetch" / "403 Not authenticated".
        # Solusi: hilangkan "*", daftarkan origin secara eksplisit.
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_FOLDER", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# OCR PREPROCESSING (DIPERBAIKI - OPTIMASI)
# ═══════════════════════════════════════════════════════════════════

from PIL import Image, ImageEnhance
from typing import List, Dict, Tuple

# NOTE: logger sudah didefinisikan di atas (line ~39), tidak perlu deklarasi ulang

def preprocess_image_simple(image_bytes: bytes) -> list:
    """Preprocessing lebih agresif untuk OCR - OPTIMASI"""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    
    results = []
    
    # 🔥 Potong lebih presisi (15% atas, 5% bawah)
    y_start = int(h * 0.15)
    y_end = int(h * 0.95)
    cropped = img[y_start:y_end, 0:w]
    
    # 🔥 PERBAIKI: resize 2x saja (bukan 4x) - lebih cepat
    resized = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    
    # 🔥 Grayscale
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    
    # 🔥 CLAHE lebih agresif
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    
    # 🔥 Sharpening
    kernel = np.array([[-1,-1,-1],
                       [-1, 9,-1],
                       [-1,-1,-1]])
    sharpened = cv2.filter2D(enhanced, -1, kernel)
    
    # 🔥 Denoising
    denoised = cv2.fastNlMeansDenoising(sharpened, h=30)
    
    # 🔥 Adaptive threshold
    binary = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                    cv2.THRESH_BINARY, 15, 8)
    
    # 🔥 Morphology
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)
    
    # 🔥 KURANGI VARIASI - hanya 2 jenis untuk kecepatan
    results.append(Image.fromarray(cleaned))
    results.append(Image.fromarray(enhanced))
    
    return results

def tesseract_extract(image_bytes: bytes) -> str:
    """
    Nama fungsi tetap dipertahankan supaya endpoint
    tidak perlu diubah.
    OCR engine menggunakan Tesseract.
    """

    best_text = ""
    best_score = 0

    try:
        images = preprocess_image_simple(image_bytes)

        # 🔥 KURANGI KONFIGURASI - hanya 3 yang paling penting
        configs = [
            "--oem 3 --psm 6",   # default, block text - PALING PENTING
            "--oem 3 --psm 4",   # assume single column
            "--oem 3 --psm 11",  # sparse text
        ]

        for img in images:
            for config in configs:
                try:
                    text = pytesseract.image_to_string(img, config=config)
                    # 🔥 CEK ADA TIDAK ANGKA 1.xxx atau 2.xxx (distance)
                    has_distance = len(re.findall(r'[1-4]\.\d{3,5}', text))
                    decimal_score = len(re.findall(r'\d+\.\d{4,}', text))
                    total_score = decimal_score + has_distance * 5  # distance lebih penting

                    if total_score > best_score:
                        best_score = total_score
                        best_text = text
                        logger.info(f"Found config with score {total_score}: {config}")
                except Exception:
                    continue

        custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist="0123456789.- "'
        for img in images[:2]:
            try:
                text = pytesseract.image_to_string(img, config=custom_config)
                score = len(re.findall(r'\d+\.\d{3,}', text))
                if score > best_score:
                    best_score = score
                    best_text = text
            except:
                pass

        logger.info(f"Tesseract extracted {len(best_text)} chars, score={best_score}")
        return best_text

    except Exception as e:
        logger.error(f"Tesseract error: {e}")
        return ""

def ocr_space_extract(image_bytes: bytes) -> str:
    """Ekstrak teks menggunakan OCR.space API"""
    # 🔥 GANTI DENGAN API KEY ANDA (gunakan environment variable)
    OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "65299172ed88957")
    
    try:
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'file': ('otdr.jpg', image_bytes)},
            data={
                'apikey': OCR_SPACE_API_KEY,
                'language': 'eng',
                'isTable': True,
                'scale': True,
                'OCREngine': 2,
            },
            timeout=60,  # 🔥 DINAIIKKAN dari 30 ke 60
        )
        result = response.json()
        
        # 🔥 CEK ERROR
        if result.get('ErrorMessage'):
            logger.error(f"OCR.space Error: {result.get('ErrorMessage')}")
            return ""
            
        if result.get('ParsedResults') and len(result['ParsedResults']) > 0:
            text = result['ParsedResults'][0]['ParsedText']
            logger.info(f"OCR.space extracted {len(text)} chars")
            return text
        return ""
    except Exception as e:
        logger.error(f"OCR.space error: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# PARSER HORIZONTAL (UNTUK FORMAT TRANSPOSE)
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_horizontal(raw_text: str) -> Tuple[List[Dict], float]:
    """
    Parse OCR OTDR format horizontal (transpose)
    """
    lines = raw_text.split('\n')
    
    data = {
        'distance': [],
        'section': [],
        'loss': [],
        'total_l': [],
        'avg_l': [],
        'return': []
    }
    
    for line in lines:
        if 'Distance km' in line or 'Distance' in line:
            nums = re.findall(r'(\d+\.\d+)', line)
            if nums:
                data['distance'] = [float(n) for n in nums if float(n) > 0]
                
        elif 'Section km' in line or 'Section' in line:
            nums = re.findall(r'(\d+\.\d+)', line)
            if nums:
                data['section'] = [float(n) for n in nums if float(n) > 0]
                
        elif 'Loss dB' in line or 'Loss' in line:
            nums = re.findall(r'(\d+\.\d+)', line)
            if nums:
                data['loss'] = [float(n) for n in nums if float(n) >= 0]
                
        elif 'Total-L' in line:
            nums = re.findall(r'(\d+\.\d+)', line)
            if nums:
                data['total_l'] = [float(n) for n in nums if float(n) >= 0]
                
        elif 'Avg.L' in line or 'Avg.L dB/km' in line:
            nums = re.findall(r'(\d+\.\d+)', line)
            if nums:
                data['avg_l'] = [float(n) for n in nums if float(n) > 0]
                
        elif 'Return dB' in line or 'Return' in line:
            nums = re.findall(r'(\d+\.\d+)', line)
            if nums:
                data['return'] = [float(n) for n in nums]
    
    logger.info(f"Extracted horizontal data:")
    logger.info(f"  Distance: {data['distance']}")
    logger.info(f"  Loss: {data['loss']}")
    logger.info(f"  Total-L: {data['total_l']}")
    logger.info(f"  Avg-L: {data['avg_l']}")
    logger.info(f"  Return: {data['return']}")
    
    rows = []
    
    # Skip index 0 (starting point)
    for i in range(1, min(5, len(data['distance']))):
        # 🔥 PERBAIKI: KM4 loss = None (End of Fiber)
        is_km4 = (i == 4)
        loss_value = None if is_km4 else (data['loss'][i-1] if i-1 < len(data['loss']) else 0.0)
        
        row = {
            'distance': data['distance'][i] if i < len(data['distance']) else float(i),
            'section': data['section'][i] if i < len(data['section']) else 0.0,
            'loss': loss_value,  # 🔥 KM4 = None
            'total_l': data['total_l'][i] if i < len(data['total_l']) else 0.0,
            'avg_l': data['avg_l'][i-1] if i-1 < len(data['avg_l']) else 0.0,
            'return': -abs(data['return'][i]) if i < len(data['return']) else -45.0
        }
        rows.append(row)
    
    # 🔥 PASTIKAN KM4 loss = None
    if len(rows) >= 4:
        rows[3]['loss'] = None
    
    # Cari Avg Total
    avg_total = 0.0
    match_avg = re.search(r'Avg\.L\s+(\d+\.\d+)dB/km', raw_text)
    if match_avg:
        avg_total = float(match_avg.group(1))
    
    if avg_total == 0.0:
        match_avg = re.search(r'Avg\.L\s+(\d+\.\d+)', raw_text)
        if match_avg:
            avg_total = float(match_avg.group(1))
    
    if avg_total == 0.0 and rows:
        total_losses = [row['total_l'] for row in rows if row['total_l'] > 0]
        if total_losses:
            avg_total = sum(total_losses) / len(total_losses)
    
    return rows, avg_total


# ═══════════════════════════════════════════════════════════════════
# PARSER MANUAL (LAST RESORT)
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_manual(raw_text: str) -> Tuple[List[Dict], float]:
    """
    Parse manual dengan ekstraksi nilai langsung dari teks
    """
    rows = []
    
    # Ekstrak semua angka desimal
    all_numbers = re.findall(r'(\d+\.\d+)', raw_text)
    
    logger.info(f"Manual extraction - all numbers: {all_numbers[:20]}")
    
    # Cari distance (1.x, 2.x, 3.x, 4.x)
    distances = []
    for num in all_numbers:
        fnum = float(num)
        if 0.8 <= fnum <= 4.2 and fnum not in distances:
            distances.append(fnum)
    distances.sort()
    
    # Cari loss (0.1 - 5.0) - hanya untuk KM1-KM3
    losses = []
    for num in all_numbers:
        fnum = float(num)
        if 0.1 <= fnum <= 5.0 and fnum not in distances:
            losses.append(fnum)
    
    # Cari total_l (0.5 - 10.0)
    total_ls = []
    for num in all_numbers:
        fnum = float(num)
        if 0.5 <= fnum <= 10.0 and fnum not in distances and fnum not in losses:
            total_ls.append(fnum)
    
    logger.info(f"Manual extraction - distances: {distances[:5]}")
    logger.info(f"Manual extraction - losses: {losses[:5]}")
    logger.info(f"Manual extraction - total_ls: {total_ls[:5]}")
    
    # Build rows
    for i in range(1, 5):
        dist = distances[i-1] if i-1 < len(distances) else float(i)
        
        # 🔥 PERBAIKI: KM4 loss = None (End of Fiber)
        is_km4 = (i == 4)
        loss = None if is_km4 else (losses[i-1] if i-1 < len(losses) else 0.0)
        
        total_l = total_ls[i] if i < len(total_ls) else 0.0
        
        rows.append({
            'distance': dist,
            'section': 0.0,
            'loss': loss,  # 🔥 KM4 = None
            'total_l': total_l,
            'avg_l': total_l / dist if dist > 0 else 0.0,
            'return': -45.0
        })
    
    # 🔥 PASTIKAN KM4 loss = None
    if len(rows) >= 4:
        rows[3]['loss'] = None
    
    # Cari avg total
    avg_total = 0.0
    match_avg = re.search(r'Avg\.L\s+(\d+\.\d+)', raw_text)
    if match_avg:
        avg_total = float(match_avg.group(1))
    
    return rows, avg_total


# ═══════════════════════════════════════════════════════════════════
# VALIDASI HASIL PARSING (DIPERBAIKI)
# ═══════════════════════════════════════════════════════════════════

def is_valid_parsed_rows(rows: list) -> bool:
    """
    Cek apakah hasil parsing valid (minimal 3 dari 4 baris punya data)
    KM4 tidak dihitung untuk loss karena End of Fiber
    """
    if len(rows) < 4:
        return False
    
    valid_count = 0
    for i, row in enumerate(rows):
        loss_val = row.get('loss')
        total_val = row.get('total_l', 0)
        
        # KM4: cek total_l saja (loss selalu None)
        if i == 3:
            if total_val > 0:
                valid_count += 1
        else:
            if (loss_val is not None and loss_val > 0) or total_val > 0:
                valid_count += 1

    # Minimal 3 dari 4 valid — KECUALI Fiber Cut KM2 yang hanya punya 2 rows real
    # Deteksi: KM1 valid, KM2 loss=None tapi total_l>0, KM3+KM4 semua 0
    if valid_count == 2:
        km1_ok  = (rows[0].get('loss', 0) or 0) > 0 or rows[0].get('total_l', 0) > 0
        km2_cut = rows[1].get('loss') is None and rows[1].get('total_l', 0) > 0
        km3_empty = rows[2].get('total_l', 0) == 0
        km4_empty = rows[3].get('total_l', 0) == 0
        if km1_ok and km2_cut and km3_empty and km4_empty:
            return True  # Fiber Cut KM2 — valid dengan 2 rows

    return valid_count >= 3


# ═══════════════════════════════════════════════════════════════════
# HYBRID PARSER (MENGGABUNGKAN 3 STRATEGI) - DIPERBAIKI
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_hybrid(raw_text: str) -> Tuple[List[Dict], float]:
    """
    Hybrid parser dengan 3 strategi: vertical, horizontal, manual
    """
    logger.info("🔄 Starting hybrid parser...")
    
    # Strategy 1: Vertical (format tabel normal)
    try:
        logger.info("📊 Trying vertical parser...")
        rows, avg = parse_otdr_table_simple(raw_text)
        # 🔥 PASTIKAN KM4 loss = None
        if len(rows) >= 4:
            rows[3]['loss'] = None
        if is_valid_parsed_rows(rows):
            logger.info(f"✅ Vertical parser success: {sum(1 for r in rows if (r.get('loss') is not None and r.get('loss') > 0) or r.get('total_l', 0) > 0)}/4 rows valid")
            return rows, avg
        else:
            logger.info(f"⚠️ Vertical parser only {sum(1 for r in rows if (r.get('loss') is not None and r.get('loss') > 0) or r.get('total_l', 0) > 0)}/4 valid")
    except Exception as e:
        logger.warning(f"Vertical parser failed: {e}")
        import traceback
        logger.warning(traceback.format_exc())
    
    # Strategy 2: Horizontal (format transpose)
    try:
        logger.info("📊 Trying horizontal parser...")
        rows, avg = parse_otdr_horizontal(raw_text)
        # 🔥 PASTIKAN KM4 loss = None
        if len(rows) >= 4:
            rows[3]['loss'] = None
        if is_valid_parsed_rows(rows):
            logger.info(f"✅ Horizontal parser success: {sum(1 for r in rows if (r.get('loss') is not None and r.get('loss') > 0) or r.get('total_l', 0) > 0)}/4 rows valid")
            return rows, avg
        else:
            logger.info(f"⚠️ Horizontal parser only {sum(1 for r in rows if (r.get('loss') is not None and r.get('loss') > 0) or r.get('total_l', 0) > 0)}/4 valid")
    except Exception as e:
        logger.warning(f"Horizontal parser failed: {e}")
        import traceback
        logger.warning(traceback.format_exc())
    
    # Strategy 3: Manual extraction
    try:
        logger.info("📊 Trying manual parser...")
        rows, avg = parse_otdr_manual(raw_text)
        # 🔥 PASTIKAN KM4 loss = None
        if len(rows) >= 4:
            rows[3]['loss'] = None
        if is_valid_parsed_rows(rows):
            logger.info(f"✅ Manual parser success: {sum(1 for r in rows if (r.get('loss') is not None and r.get('loss') > 0) or r.get('total_l', 0) > 0)}/4 rows valid")
            return rows, avg
        else:
            logger.info(f"⚠️ Manual parser only {sum(1 for r in rows if (r.get('loss') is not None and r.get('loss') > 0) or r.get('total_l', 0) > 0)}/4 valid")
    except Exception as e:
        logger.warning(f"Manual parser failed: {e}")
        import traceback
        logger.warning(traceback.format_exc())
    
    # Fallback: return empty rows dengan KM4 loss = None
    logger.warning("❌ All parsers failed, returning empty rows")
    return [
        {'distance': 1.0, 'section': 0.0, 'loss': 0.0, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
        {'distance': 2.0, 'section': 0.0, 'loss': 0.0, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
        {'distance': 3.0, 'section': 0.0, 'loss': 0.0, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
        {'distance': 4.0, 'section': 0.0, 'loss': None, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0}
    ], 0.0


# ═══════════════════════════════════════════════════════════════════
# OTDR PARSER (DIPERBAIKI - TANPA ROUND)
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# OTDR PARSER (DIPERBAIKI - FIBER CUT AWARE)
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_table_simple(raw_text: str) -> Tuple[List[Dict], float]:
    """
    Parse teks OCR OTDR.
    Pendekatan bersih: tidak ada swap kolom, deteksi '---' per posisi kolom
    agar posisi angka tidak bergeser saat ada nilai yang tidak terbaca.
    """
    text = raw_text.replace(",", ".")

    # =====================================================
    # 1. AVG TOTAL
    # =====================================================
    avg_total = 0.0
    m = re.search(r'Avg\.?\s*L?\s*[:=]?\s*(\d+\.\d{2,})\s*dB/km', text, re.IGNORECASE)
    if m:
        avg_total = float(m.group(1))
    else:
        m2 = re.search(r'(\d+\.\d{2,})\s*dB/km', text)
        if m2:
            avg_total = float(m2.group(1))

    # =====================================================
    # 2. TOTAL EVENTS — sinyal paling akurat untuk Fiber Cut
    # =====================================================
    total_events = 4
    te_match = re.search(r'Total Events[:=]\s*(\d+)', text, re.IGNORECASE)
    if te_match:
        total_events = int(te_match.group(1))
        logger.info(f"[TOTAL EVENTS] = {total_events}")

    # =====================================================
    # 3. CLEAN LINES
    # =====================================================
    lines = []
    for line in text.splitlines():
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            lines.append(line)

    # =====================================================
    # 4. DETECT EVENT LINES (harus ada distance 1.xxx - 4.xxx)
    # =====================================================
    event_lines = []
    for line in lines:
        if re.search(r'\b[1-4]\.\d{3,5}\b', line):
            event_lines.append(line)

    # =====================================================
    # 5. MERGE OCR BROKEN ROW
    # =====================================================
    merged = []
    i = 0
    while i < len(event_lines):
        row = event_lines[i]
        nums = re.findall(r'\d+\.\d+', row)
        while len(nums) < 5 and i + 1 < len(event_lines):
            i += 1
            row += " " + event_lines[i]
            nums = re.findall(r'\d+\.\d+', row)
        merged.append(row)
        i += 1

    # =====================================================
    # 6. PARSE EACH ROW — mempertahankan posisi kolom
    #    '---' diganti __MISSING__ agar angka tidak bergeser
    # =====================================================
    rows = []
    for idx, row in enumerate(merged):
        # Ganti '---' dengan sentinel sebelum extract angka
        row_norm = re.sub(r'---+|—|–', ' __MISSING__ ', row)

        # Extract token: angka atau sentinel
        raw_tokens = re.findall(r'__MISSING__|-?\d+\.?\d*', row_norm)

        # Konversi ke list nilai (None untuk missing, float untuk angka)
        values = []
        for t in raw_tokens:
            if t == '__MISSING__':
                values.append(None)
            else:
                try:
                    f = float(t)
                    if abs(f) < 10000:
                        values.append(f)
                except:
                    pass

        if not values:
            continue

        # Cari distance index (1.xxx - 4.xxx dengan 3+ desimal)
        distance_idx = None
        for j, val in enumerate(values):
            if val is not None and 0.8 <= val <= 4.5:
                val_str = str(val)
                if '.' in val_str and len(val_str.split('.')[1]) >= 3:
                    distance_idx = j
                    break

        if distance_idx is None:
            for j, val in enumerate(values):
                if val is not None and 0.8 <= val <= 4.5:
                    distance_idx = j
                    break

        if distance_idx is None:
            continue

        values = values[distance_idx:]

        # Format kolom: [distance, section, loss, total_l, avg_l, return]
        # None di posisi 2 (loss) → '---' di kolom Loss → Fiber Cut / End of Fiber
        # None di posisi 5 (return) → '---' di kolom Return → non-reflektif (normal)
        if len(values) >= 6:
            distance    = values[0]
            section     = values[1] if values[1] is not None else 0.0
            loss        = values[2]  # None jika '---' di kolom Loss
            total_l     = values[3] if values[3] is not None else 0.0
            avg_l       = values[4] if values[4] is not None else 0.0
            return_val  = values[5]  # None jika tidak ada → tidak di-default
            loss_missing = (values[2] is None)

        elif len(values) == 5:
            distance = values[0]
            section  = values[1] if values[1] is not None else 0.0
            if values[2] is None:
                # None di posisi loss → '---' adalah loss
                loss        = None
                total_l     = values[3] if values[3] is not None else 0.0
                avg_l       = values[4] if values[4] is not None else 0.0
                return_val  = None   # return tidak ada
                loss_missing = True
            else:
                # '---' bukan di loss → mungkin return tidak ada
                loss        = values[2]
                total_l     = values[3] if values[3] is not None else 0.0
                avg_l       = values[4] if values[4] is not None else 0.0
                return_val  = None   # return tidak ada
                loss_missing = False
        else:
            continue

        if distance is None:
            continue

        rows.append({
            "distance":     distance,
            "section":      section,
            "loss":         loss,
            "total_l":      total_l,
            "avg_l":        avg_l,
            "return":       -abs(return_val) if return_val is not None and return_val != 0 else None,
            "loss_missing": loss_missing,
        })

    # =====================================================
    # 7. SORT BY DISTANCE
    # =====================================================
    rows = sorted(rows, key=lambda x: x["distance"])

    # =====================================================
    # 7b. FIX KOLOM BERGESER — OCR sering tidak membaca '---'
    #     Berlaku untuk KM3 (fiber cut) dan KM4 (end of fiber).
    #     Harus dilakukan SEBELUM fiber cut detection (step 9)
    #     agar loss masih bernilai numerik saat dicek.
    # =====================================================

    # FIX KM3 — untuk kasus Fiber Cut KM3 dimana '---' tidak terbaca
    if len(rows) >= 3:
        km3 = rows[2]
        lv3 = km3.get('loss'); tl3 = km3.get('total_l', 0); al3 = km3.get('avg_l', 0)
        if (lv3 is not None and tl3 < 5.0 and (abs(al3) > 5.0 or al3 < 0)):
            logger.info(f"  KM3 FIX: '---' tidak terbaca OCR, geser kolom balik")
            logger.info(f"  KM3 before: loss={lv3}, total_l={tl3}, avg_l={al3}")
            km3['loss']    = None
            km3['total_l'] = lv3
            km3['avg_l']   = abs(tl3)
            km3['return']  = -abs(al3) if al3 and al3 != 0 else None
            km3['loss_missing'] = True
            logger.info(f"  KM3 after : loss=None, total_l={km3['total_l']}, avg_l={km3['avg_l']}, return={km3['return']}")

    # FIX KM4 — untuk End of Fiber dimana '---' tidak terbaca
    if len(rows) >= 4:
        km4 = rows[3]
        # Kondisi: loss ada nilai (harusnya None), total_l kecil (< 5), 
        # dan abs(avg_l) > 5 ATAU avg_l negatif (nilai return masuk ke posisi avg_l)
        loss_val = km4.get('loss')
        tl_val   = km4.get('total_l', 0)
        al_val   = km4.get('avg_l', 0)
        if (loss_val is not None and
            tl_val < 5.0 and
            (abs(al_val) > 5.0 or al_val < 0)):
            logger.info(f"  KM4 FIX: '---' tidak terbaca OCR, geser kolom balik")
            logger.info(f"  KM4 before: loss={loss_val}, total_l={tl_val}, avg_l={al_val}, return={km4['return']}")
            km4['loss']    = None
            km4['total_l'] = loss_val      # nilai loss asli → total_l
            km4['avg_l']   = abs(tl_val)   # nilai total_l asli → avg_l
            km4["return"]  = -abs(al_val) if al_val and al_val != 0 else None
            km4['loss_missing'] = True
            logger.info(f"  KM4 after : loss=None, total_l={km4['total_l']}, avg_l={km4['avg_l']}, return={km4['return']}")

    # =====================================================
    # 8. PASTIKAN NILAI POSITIF (loss, total_l, avg_l)
    #    TIDAK ADA SWAP KOLOM — nilai diambil apa adanya dari OCR
    # =====================================================
    for i, row in enumerate(rows):
        if row['loss'] is not None:
            row['loss'] = abs(row['loss'])
        row['total_l'] = abs(row['total_l'])
        row['avg_l']   = abs(row['avg_l'])

    # =====================================================
    # 9. FIBER CUT DETECTION — dari Total Events (akurat)
    #    dan loss_missing (posisi '---' di kolom Loss)
    # =====================================================
    is_fiber_cut = False
    cut_km = -1

    if total_events == 2:
        is_fiber_cut = True
        cut_km = 2
        logger.info("🔴 FIBER CUT KM2 (Total Events:2)")
    elif total_events == 3:
        # Verifikasi: KM3 harus punya loss_missing=True
        if len(rows) >= 3 and rows[2].get('loss_missing') and rows[2].get('total_l', 0) > 0:
            is_fiber_cut = True
            cut_km = 3
            logger.info("🔴 FIBER CUT KM3 (Total Events:3, loss KM3 missing)")
        elif len(rows) >= 3 and rows[2].get('total_l', 0) > 0:
            # Total Events:3 tapi loss KM3 ada nilai → tetap fiber cut
            is_fiber_cut = True
            cut_km = 3
            rows[2]['loss'] = None
            logger.info("🔴 FIBER CUT KM3 (Total Events:3)")

    # Fallback: cek loss_missing per row (tanpa mengandalkan nilai numerik)
    if not is_fiber_cut:
        for i, row in enumerate(rows[:3]):
            if row.get('loss_missing') and row.get('total_l', 0) > 0:
                is_fiber_cut = True
                cut_km = i + 1
                logger.info(f"🔴 FIBER CUT KM{cut_km} (loss_missing detected)")
                break

    # =====================================================
    # 10. NORMALISASI FIBER CUT — pad/trim rows
    # =====================================================
    if is_fiber_cut and cut_km == 2:
        if len(rows) >= 2:
            rows[1]['loss'] = None
        while len(rows) > 2:
            rows.pop()
        last_dist = rows[-1]['distance'] if rows else 2.0
        rows.append({"distance": last_dist + 1.0, "section": 0.0, "loss": None,
                     "total_l": 0.0, "avg_l": 0.0, "return": None, "loss_missing": True})
        rows.append({"distance": last_dist + 2.0, "section": 0.0, "loss": None,
                     "total_l": 0.0, "avg_l": 0.0, "return": None, "loss_missing": True})

    elif is_fiber_cut and cut_km == 3:
        if len(rows) >= 3:
            rows[2]['loss'] = None  # sudah difix di step 7b jika kolom bergeser
        while len(rows) > 3:
            rows.pop()
        if len(rows) < 4:
            last_dist = rows[-1]['distance'] if rows else 3.0
            rows.append({"distance": last_dist + 1.0, "section": 0.0, "loss": None,
                         "total_l": 0.0, "avg_l": 0.0, "return": None, "loss_missing": True})

    # KM4 loss selalu None (End of Fiber)
    if len(rows) >= 4:
        rows[3]['loss'] = None

    # =====================================================
    # 11. PASTIKAN 4 ROWS
    # =====================================================
    while len(rows) < 4:
        km = len(rows) + 1
        last_dist = rows[-1]["distance"] if rows else float(km - 1)
        rows.append({
            "distance": last_dist + 1.0, "section": 0.0,
            "loss": None,
            "total_l": 0.0, "avg_l": 0.0, "return": None,
            "loss_missing": True,
        })

    # =====================================================
    # 12. LOG HASIL
    # =====================================================
    logger.info("===== FINAL PARSED ROWS =====")
    for i, r in enumerate(rows, start=1):
        logger.info(
            f"  KM{i}: dist={r['distance']}, loss={r['loss']}, "
            f"total_l={r['total_l']}, avg_l={r['avg_l']}, return={r['return']}, "
            f"loss_missing={r.get('loss_missing', False)}"
        )
    logger.info(f"AVG TOTAL = {avg_total}, FIBER_CUT = {is_fiber_cut}, CUT_KM = {cut_km}")

    return rows, avg_total


# ═══════════════════════════════════════════════════════════════════
# EKSTRAK PRX
# ═══════════════════════════════════════════════════════════════════

def extract_prx(text: str) -> float:
    """Ekstrak Prx value dari teks"""
    m = re.search(r'Prx\s*[:=]?\s*([-\d.]+)\s*dBm', text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


# ═══════════════════════════════════════════════════════════════════
# OCR PARSER ONLY - TANPA KLASIFIKASI ML (DIPERBAIKI)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/parse-ocr")
async def parse_ocr_only(
    file: UploadFile = File(...),
    prx_manual: float = Form(None),
):
    """
    Endpoint untuk OCR parsing SAJA - tanpa klasifikasi ML.
    Mengembalikan data mentah hasil ekstraksi untuk diedit user.
    """
    allowed = {"image/jpeg", "image/png", "image/jpg", "image/bmp", "image/tiff"}
    if file.content_type not in allowed:
        return {
            "success": False,
            "error": "Format gambar tidak didukung. Gunakan JPG atau PNG.",
            "extracted": {
                "distances": [0, 0, 0, 0],
                "losses": [0, 0, 0, 0],
                "total_ls": [0, 0, 0, 0],
                "avg_ls": [0, 0, 0, 0],
                "returns": [0, 0, 0, 0],
                "avg_total": 0,
            },
            "prx": prx_manual if prx_manual else -25.0,
            "per_km": {
                "km1": {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km2": {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km3": {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km4": {"distance": 0, "loss": None, "total_l": 0, "avg_l": 0, "return": 0},
            }
        }
    
    content = await file.read()
    raw_text = ""
    ocr_method = "none"
    
    logger.info("=" * 70)
    logger.info("🔄 Starting OCR Parser (NO ML)...")
    
    try:
        results = {}
        
        # 🔥 TIMEOUT TESSERACT
        try:
            results['tesseract'] = await asyncio.wait_for(
                asyncio.to_thread(tesseract_extract, content), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning("Tesseract timed out")
            results['tesseract'] = ""

        # 🔥 TIMEOUT OCR.SPACE - dinaikkan
        try:
            results['ocr.space'] = await asyncio.wait_for(
                asyncio.to_thread(ocr_space_extract, content), timeout=120.0)
        except asyncio.TimeoutError:
            logger.warning("OCR.space timed out")
            results['ocr.space'] = ""

        best_score = 0
        for method, text in results.items():
            if text:
                score = len(re.findall(r'\d+\.\d{3,}', text))
                if score > best_score:
                    best_score = score
                    raw_text = text
                    ocr_method = method

        logger.info(f"✅ Best OCR: {ocr_method} with {best_score} decimal numbers")

    except Exception as e:
        logger.error(f"OCR error: {e}")
    
    # 🔥 RETURN DENGAN STRUKTUR YANG KONSISTEN
    if not raw_text or len(raw_text.strip()) < 20:
        return {
            "success": False,
            "error": "Gambar tidak dapat dibaca. Pastikan foto jelas dan tabel OTDR terlihat.",
            "needs_manual": True,
            "message": "OCR gagal membaca gambar. Silakan input data secara manual.",
            "extracted": {
                "distances": [0, 0, 0, 0],
                "losses": [0, 0, 0, 0],
                "total_ls": [0, 0, 0, 0],
                "avg_ls": [0, 0, 0, 0],
                "returns": [0, 0, 0, 0],
                "avg_total": 0,
            },
            "prx": prx_manual if prx_manual else -25.0,
            "per_km": {
                "km1": {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km2": {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km3": {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km4": {"distance": 0, "loss": None, "total_l": 0, "avg_l": 0, "return": 0},
            }
        }
    
    logger.info(f"📝 RAW TEXT ({ocr_method}):\n{raw_text[:500]}")
    
    rows, avg_total = parse_otdr_hybrid(raw_text)
    
    # 🔥 AUTO-DETECT FIBER CUT
    mode, cut_km = detect_measurement_mode_from_rows(rows)
    logger.info(f"[AUTO-DETECT] OCR mode: {mode}, cut_km: {cut_km}")
    
    # 🔥 Jika Fiber Cut, set loss di rows sesuai mode
    if mode == 'fiber_cut_km2':
        if len(rows) >= 2:
            rows[1]['loss'] = None  # KM2 loss = None
        if len(rows) >= 3:
            rows[2]['loss'] = None  # KM3 loss = None
        if len(rows) >= 4:
            rows[3]['loss'] = None  # KM4 loss = None
    elif mode == 'fiber_cut_km3':
        if len(rows) >= 3:
            rows[2]['loss'] = None  # KM3 loss = None
        if len(rows) >= 4:
            rows[3]['loss'] = None  # KM4 loss = None
    
    # 🔥 PASTIKAN KM4 loss = None (End of Fiber)
    if len(rows) >= 4:
        rows[3]['loss'] = None
    
    prx_from_ocr = extract_prx(raw_text)
    final_prx = prx_manual if prx_manual is not None else (prx_from_ocr if prx_from_ocr else -25.0)
    
    logger.info(f"📊 Parsed rows: {len(rows)}")
    for i, row in enumerate(rows):
        logger.info(f"   KM{i+1}: dist={row['distance']}, loss={row['loss']}, total_l={row['total_l']}")
    
    # 🔥 DEBUG: Pastikan loss KM3 = None untuk Fiber Cut
    if mode == 'fiber_cut_km3' and len(rows) >= 3:
        logger.info(f"🔍 DEBUG: KM3 loss = {rows[2].get('loss')} (type: {type(rows[2].get('loss'))})")
    
    # 🔥 VALIDASI DENGAN AMAN
    valid = [r for r in rows if r['distance'] > 0.5]
    if len(valid) < 2:
        # 🔥 PASTIKAN 4 ROWS UNTUK RESPONSE
        while len(rows) < 4:
            km = len(rows) + 1
            rows.append({
                "distance": float(km),
                "section": 0.0,
                "loss": None if km == 4 else 0.0,
                "total_l": 0.0,
                "avg_l": 0.0,
                "return": -45.0
            })
        
        return {
            "success": False,
            "error": f"Hanya {len(valid)} baris valid terdeteksi (butuh minimal 2).",
            "needs_manual": True,
            "message": "Data yang terdeteksi tidak lengkap. Silakan input data secara manual.",
            "extracted": {
                "distances": [rows[i]['distance'] if i < len(rows) else 0 for i in range(4)],
                "losses": [rows[i]['loss'] if i < len(rows) else None for i in range(4)],
                "total_ls": [rows[i]['total_l'] if i < len(rows) else 0 for i in range(4)],
                "avg_ls": [rows[i]['avg_l'] if i < len(rows) else 0 for i in range(4)],
                "returns": [rows[i]['return'] if i < len(rows) else 0 for i in range(4)],
                "avg_total": avg_total if avg_total else 0,
            },
            "prx": final_prx,
            "per_km": {
                "km1": rows[0] if len(rows) > 0 else {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km2": rows[1] if len(rows) > 1 else {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km3": rows[2] if len(rows) > 2 else {"distance": 0, "loss": 0, "total_l": 0, "avg_l": 0, "return": 0},
                "km4": rows[3] if len(rows) > 3 else {"distance": 0, "loss": None, "total_l": 0, "avg_l": 0, "return": 0},
            }
        }
    
    # 🔥 PASTIKAN 4 ROWS UNTUK RESPONSE
    while len(rows) < 4:
        km = len(rows) + 1
        rows.append({
            "distance": float(km),
            "section": 0.0,
            "loss": None if km == 4 else 0.0,
            "total_l": 0.0,
            "avg_l": 0.0,
            "return": -45.0
        })
    
    # 🔥 Pastikan loss untuk Fiber Cut tetap None di response
    # (mencegah perubahan None menjadi 0 di proses mapping)
    loss_values = []
    for i, row in enumerate(rows):
        loss_val = row.get('loss')
        # 🔥 Jika loss adalah 0 dan ini adalah Fiber Cut di KM3, tetap None
        if mode == 'fiber_cut_km3' and i == 2 and (loss_val == 0 or loss_val is None):
            loss_values.append(None)
        elif mode == 'fiber_cut_km2' and i >= 1 and (loss_val == 0 or loss_val is None):
            loss_values.append(None)
        else:
            loss_values.append(loss_val)
    
    logger.info("=" * 70)
    logger.info("✅ OCR parsing completed (NO ML classification)")
    
    return {
        "success": True,
        "message": "OCR berhasil diekstrak. Silakan periksa dan edit data sebelum klasifikasi.",
        "raw_text": raw_text[:500],
        "ocr_method": ocr_method,
        "detected_mode": mode,  
        "cut_km": cut_km, 
        "extracted": {
            "distances": [rows[i]['distance'] for i in range(4)],
            "losses": loss_values,  # 🔥 GUNAKAN loss_values yang sudah dipastikan None
            "total_ls": [rows[i]['total_l'] for i in range(4)],
            "avg_ls": [rows[i]['avg_l'] for i in range(4)],
            "returns": [rows[i]['return'] for i in range(4)],
            "avg_total": avg_total if avg_total else 0,
        },
        "prx": final_prx,
        "per_km": {"km1": rows[0], "km2": rows[1], "km3": rows[2], "km4": rows[3]},
    }

# ═══════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/register", status_code=201)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email sudah terdaftar")
    
    result = await db.execute(select(func.count(User.id)))
    user_count = result.scalar() or 0
    is_admin = user_count == 0
    is_approved = is_admin
    
    new_user = User(
        email=payload.email,
        password=hash_password(payload.password),
        name=payload.name,
        is_approved=is_approved,
        is_admin=is_admin,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    if is_admin:
        return {"message": "Akun admin berhasil dibuat. Silakan login.", "user_id": new_user.id}
    else:
        return {"message": "Akun berhasil dibuat. Menunggu persetujuan admin.", "user_id": new_user.id}

@app.post("/api/login", response_model=TokenResponse)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="Email atau password salah")
    
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Akun belum disetujui admin.")
    
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserOut(
            id=user.id,
            email=user.email,
            name=user.name,
            is_admin=user.is_admin,
            is_approved=user.is_approved,
        )
    )

@app.get("/api/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        is_admin=current_user.is_admin,
        is_approved=current_user.is_approved,
    )

@app.get("/api/check-status")
async def check_approval_status(email: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        return {"exists": False, "is_approved": False}
    return {
        "exists": True,
        "is_approved": user.is_approved,
        "is_admin": user.is_admin,
        "message": "Akun sudah disetujui" if user.is_approved else "Menunggu persetujuan admin",
    }

# ═══════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/admin/users")
async def get_pending_users(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(User).where(User.is_approved == False, User.is_admin == False)
    )
    users = result.scalars().all()
    return {"users": [{"id": u.id, "email": u.email, "name": u.name, "created_at": u.created_at.isoformat() if u.created_at else None} for u in users]}

@app.get("/api/admin/users/all")
async def get_all_users(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {"users": [{"id": u.id, "email": u.email, "name": u.name, "is_approved": u.is_approved, "is_admin": u.is_admin, "created_at": u.created_at.isoformat() if u.created_at else None} for u in users]}

@app.post("/api/admin/approve/{user_id}")
async def approve_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    user.is_approved = True
    await db.commit()
    return {"message": f"User {user.email} berhasil disetujui"}

@app.delete("/api/admin/reject/{user_id}")
async def reject_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    await db.delete(user)
    await db.commit()
    return {"message": f"User {user.email} berhasil dihapus"}

# ═══════════════════════════════════════════════════════════════════
# DETECTION OCR - MAIN ENDPOINT
# ═══════════════════════════════════════════════════════════════════

# @app.post("/api/detect")
# async def detect_ocr(
#     file: UploadFile = File(...),
#     prx_manual: float = Form(None),
#     db: AsyncSession = Depends(get_db),
#     current_user: User = Depends(get_optional_user),
# ):
#     allowed = {"image/jpeg", "image/png", "image/jpg", "image/bmp", "image/tiff"}
#     if file.content_type not in allowed:
#         raise HTTPException(status_code=400, detail="Format gambar tidak didukung.")
    
#     content = await file.read()
#     raw_text = ""
#     logger.info(f"📝 RAW TEXT FULL:\n{raw_text}")
#     ocr_method = "none"
    
#     logger.info("=" * 70)
#     logger.info("🔄 Starting OCR process...")
    
#     try:
#         results = {}
        
#         try:
#             results['tesseract'] = await asyncio.wait_for(
#                 asyncio.to_thread(tesseract_extract, content), timeout=25.0)
#         except asyncio.TimeoutError:
#             logger.warning("Tesseract timed out")
#             results['tesseract'] = ""

#         try:
#             results['ocr.space'] = await asyncio.wait_for(
#                 asyncio.to_thread(ocr_space_extract, content), timeout=15.0)
#         except asyncio.TimeoutError:
#             logger.warning("OCR.space timed out")
#             results['ocr.space'] = ""

#         best_score = 0
#         for method, text in results.items():
#             if text:
#                 score = len(re.findall(r'\d+\.\d{3,}', text))
#                 if score > best_score:
#                     best_score = score
#                     raw_text = text
#                     ocr_method = method

#         logger.info(f"✅ Best OCR: {ocr_method} with {best_score} decimal numbers")

#     except Exception as e:
#         logger.error(f"OCR error: {e}")
    
#     if not raw_text or len(raw_text.strip()) < 20:
#         raise HTTPException(
#             status_code=400,
#             detail="Gambar tidak dapat dibaca. Pastikan foto jelas dan tabel OTDR terlihat."
#         )
    
#     logger.info(f"📝 RAW TEXT ({ocr_method}):\n{raw_text[:500]}")
    
#     rows, avg_total = parse_otdr_table_simple(raw_text)
#     avg_total = round(avg_total, 2)
    
#     prx_from_ocr = extract_prx(raw_text)
#     final_prx = prx_manual if prx_manual is not None else (prx_from_ocr if prx_from_ocr else -25.0)
    
#     logger.info(f"📊 Parsed rows: {len(rows)}")
#     for i, row in enumerate(rows):
#         logger.info(f"   KM{i+1}: dist={row['distance']} loss={row['loss']} total_l={row['total_l']}")
    
#     valid = [r for r in rows if r['distance'] > 0.5]
#     if len(valid) < 2:
#         raise HTTPException(
#             status_code=400,
#             detail=f"Hanya {len(valid)} baris valid terdeteksi (butuh minimal 2)."
#         )
    
#     logger.info("=" * 50)
#     logger.info("Starting ML Prediction...")
    
#     otdr_values = {
#         'Prx (dBm)': final_prx,
#         'Distance 1': rows[0]['distance'], 'Distance 2': rows[1]['distance'],
#         'Distance 3': rows[2]['distance'], 'Distance 4': rows[3]['distance'],
#         'Loss 1': rows[0]['loss'], 'Loss 2': rows[1]['loss'], 'Loss 3': rows[2]['loss'],
#         'Total-L 1': rows[0]['total_l'], 'Total-L 2': rows[1]['total_l'],
#         'Total-L 3': rows[2]['total_l'], 'Total-L 4': rows[3]['total_l'],
#         'Avg-L 1': rows[0]['avg_l'], 'Avg-L 2': rows[1]['avg_l'],
#         'Avg-L 3': rows[2]['avg_l'], 'Avg-L 4': rows[3]['avg_l'],
#         'Avg-Total': avg_total,
#         'Return 1': rows[0]['return'], 'Return 2': rows[1]['return'],
#         'Return 3': rows[2]['return'], 'Return 4': rows[3]['return'],
#     }
    
#     try:
#         pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)
#         logger.info(f"🤖 ML prediction SUCCESS: {pred.get('prediction')}")
#     except Exception as e:
#         logger.error(f"❌ ML prediction FAILED: {e}")
#         pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
    
#     logger.info("=" * 50)
#     logger.info("💾 Saving to Database...")
#     user_id = current_user.id if current_user else 1
    
#     try:
#         record = OtdrResult(
#             user_id=user_id,
#             timestamp=datetime.now(),
#             prx=final_prx,
#             loss_1=rows[0]['loss'], loss_2=rows[1]['loss'],
#             loss_3=rows[2]['loss'], loss_4=None,
#             return_1=rows[0]['return'], return_2=rows[1]['return'],
#             return_3=rows[2]['return'], return_4=rows[3]['return'],
#             distance_1=rows[0]['distance'], distance_2=rows[1]['distance'],
#             distance_3=rows[2]['distance'], distance_4=rows[3]['distance'],
#             total_l_1=rows[0]['total_l'], total_l_2=rows[1]['total_l'],
#             total_l_3=rows[2]['total_l'], total_l_4=rows[3]['total_l'],
#             avg_l_1=rows[0]['avg_l'], avg_l_2=rows[1]['avg_l'],
#             avg_l_3=rows[2]['avg_l'], avg_l_4=rows[3]['avg_l'],
#             avg_total=avg_total,
#             klasifikasi=pred.get("prediction"),
#             status=pred.get("status"),
#             confidence=pred.get("confidence"),
#             source="ocr",
#             raw_text=raw_text[:1000],
#         )
        
#         db.add(record)
#         await db.commit()
#         await db.refresh(record)
#         logger.info(f"✅ Saved to DB: ID={record.id}")
    
#         status_str = pred.get("status", "Normal")
#         if status_str.lower() in ["warning", "critical"]:
#             logger.info(f"[TELEGRAM] Mengirim alert untuk: {pred.get('prediction')}")
#             try:
#                 await asyncio.to_thread(
#                     send_telegram_alert,
#                     classification=pred.get("prediction"),
#                     status=status_str,
#                     loss=[rows[0]['loss'], rows[1]['loss'], rows[2]['loss'], rows[3]['loss']],
#                     rl=[rows[0]['return'], rows[1]['return'], rows[2]['return'], rows[3]['return']],
#                     prx=final_prx,
#                     distances=[rows[0]['distance'], rows[1]['distance'], rows[2]['distance'], rows[3]['distance']],
#                     timestamp=record.timestamp
#                 )
#                 record.telegram_alert_sent = True
#                 await db.commit()
#             except Exception as tg_err:
#                 logger.error(f"[TELEGRAM] Error: {tg_err}")
        

#     except Exception as e:
#         logger.error(f"❌ DATABASE ERROR: {e}")
#         await db.rollback()
#         raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
#     logger.info("=" * 70)
    
#     return {
#         "message": "Gambar berhasil diproses",
#         "raw_text": raw_text[:500],
#         "extracted": {
#             "distances": [rows[i]['distance'] for i in range(4)],
#             "losses": [rows[i]['loss'] for i in range(4)],
#             "total_ls": [rows[i]['total_l'] for i in range(4)],
#             "avg_ls": [rows[i]['avg_l'] for i in range(4)],
#             "returns": [rows[i]['return'] for i in range(4)],
#             "avg_total": round(avg_total, 2),
#         },
#         "per_km": {"km1": rows[0], "km2": rows[1], "km3": rows[2], "km4": rows[3]},
#         "prx": final_prx,
#         "avg_total": round(avg_total, 2),
#         "prediction": pred.get("prediction"),
#         "confidence": pred.get("confidence"),
#         "status": pred.get("status"),
#         "id": record.id,
#         "ocr_method": ocr_method,
#     }

# ═══════════════════════════════════════════════════════════════════
# DASHBOARD & HISTORY
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/dashboard")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = 100,
):
    result = await db.execute(
        select(OtdrResult)
        .where(
            (OtdrResult.source == "sheets") |
            (OtdrResult.user_id == current_user.id)
        )
        .order_by(OtdrResult.timestamp.desc())
        .limit(limit)
    )
    records = result.scalars().all()
    total = len(records)
    normal = sum(1 for r in records if r.klasifikasi == "Normal")
    
    return {
        "data": [{
            "id": r.id,
            "prx": r.prx,
            "loss_1": r.loss_1, "loss_2": r.loss_2,
            "loss_3": r.loss_3, "loss_4": r.loss_4,
            "total_l_4": r.total_l_4, 
            "return_1": r.return_1, "return_2": r.return_2,
            "return_3": r.return_3, "return_4": r.return_4,
            "distance_1": r.distance_1, "distance_2": r.distance_2,
            "distance_3": r.distance_3, "distance_4": r.distance_4,
            "klasifikasi": r.klasifikasi,
            "status": r.status,
            "confidence": r.confidence,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "source": r.source,
        } for r in records],
        "total": total,
        "normal": normal,
        "gangguan": total - normal,
    }

@app.get("/api/history")
async def get_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user),
    limit: int = 50,
    skip: int = 0,
):
    if current_user:
        result = await db.execute(
            select(OtdrResult)
            .where(
                (OtdrResult.user_id == current_user.id) |
                (OtdrResult.source == "sheets")
            )
            .order_by(OtdrResult.timestamp.desc())
            .offset(skip)
            .limit(limit)
        )
    else:
        result = await db.execute(
            select(OtdrResult)
            .where(OtdrResult.source == "sheets")
            .order_by(OtdrResult.timestamp.desc())
            .offset(skip)
            .limit(limit)
        )
    records = result.scalars().all()
    return {
        "history": [{
            "id": r.id,
            "loss_1": r.loss_1, "loss_2": r.loss_2,
            "loss_3": r.loss_3, "loss_4": r.loss_4,
            "total_l_1": r.total_l_1, "total_l_2": r.total_l_2,
            "total_l_3": r.total_l_3, "total_l_4": r.total_l_4,
            "return_1": r.return_1, "return_2": r.return_2,
            "return_3": r.return_3, "return_4": r.return_4,
            "prx": r.prx,
            "klasifikasi": r.klasifikasi,
            "status": r.status,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "source": r.source,
        } for r in records],
        "total": len(records),
    }

@app.get("/api/progress")
async def get_progress(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result_total = await db.execute(
        select(func.count(OtdrResult.id)).where(OtdrResult.user_id == current_user.id)
    )
    total_processed = result_total.scalar() or 0
    return {
        "total_processed": total_processed,
        "target_total": 2572,
        "is_complete": total_processed >= 2572,
    }

@app.get("/api/slide/{index}")
async def get_slide_data(
    index: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(OtdrResult)
        .where(
            (OtdrResult.source == "sheets") |
            (OtdrResult.user_id == current_user.id)
        )
        .order_by(OtdrResult.timestamp.asc())
        .offset(index)
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if not record:
        return await get_slide_data(0, db, current_user)
    
    total_result = await db.execute(
        select(func.count(OtdrResult.id)).where((OtdrResult.user_id == current_user.id) | (OtdrResult.source == "sheets"))
    )
    total = total_result.scalar() or 0
    
    return {
        "id": record.id,
        "current_index": index + 1,
        "total_data": total,
        "loss_1": record.loss_1, "loss_2": record.loss_2,
        "loss_3": record.loss_3, "loss_4": record.loss_4,
        "return_1": record.return_1, "return_2": record.return_2,
        "return_3": record.return_3, "return_4": record.return_4,
        "prx": record.prx,
        "klasifikasi": record.klasifikasi,
        "status": record.status,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
    }

@app.post("/api/alert")
async def trigger_alert(payload: dict):
    try:
        classification = payload.get('classification', 'unknown')
        status = payload.get('status', 'warning')
        loss = payload.get('loss', [0.2, 0.2, 0.2, 0.2])
        rl = payload.get('return_loss', [-45.0, -45.0, -45.0, -45.0])
        prx = payload.get('prx', 'N/A')
        
        await asyncio.to_thread(
            send_telegram_alert,
            classification=classification,
            status=status,
            loss=loss,
            rl=rl,
            prx=prx
        )
        return {"status": "sent"}
    except Exception as e:
        logger.error(f"[ALERT] Gagal mengirim alert manual: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/slide-alert")
async def slide_alert(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    try:
        record_id = payload.get("id")
        if not record_id:
            raise HTTPException(status_code=400, detail="Missing record id")
        
        result = await db.execute(select(OtdrResult).where(OtdrResult.id == record_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        
        status_str = record.status or ""
        if status_str.lower() not in ["warning", "critical"]:
            return {"status": "skipped", "reason": f"status is {status_str}"}
        
        loss_list = [record.loss_1, record.loss_2, record.loss_3, record.loss_4]
        rl_list = [record.return_1, record.return_2, record.return_3, record.return_4]
        
        logger.info(f"[TELEGRAM] Mengirim slide alert untuk: {record.klasifikasi} ({status_str}) ID={record_id}")
        await asyncio.to_thread(
            send_telegram_alert,
            classification=record.klasifikasi,
            status=status_str,
            loss=loss_list,
            rl=rl_list,
            prx=record.prx,
            distances=[record.distance_1, record.distance_2, record.distance_3, record.distance_4],
            timestamp=record.timestamp
        )
        
        record.telegram_alert_sent = True
        await db.commit()
        return {"status": "sent", "id": record_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SLIDE-ALERT] Gagal mengirim slide alert: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sync")
async def sync_from_sheets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(SHEET_URL, timeout=30, follow_redirects=True)
            resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal fetch Google Sheets: {str(e)}")
    
    try:
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal parse CSV: {str(e)}")
    
    df.columns = [c.strip() for c in df.columns]

    print("========== KOLOM GOOGLE SHEETS ==========")
    print(df.columns.tolist())

    print("========== CEK AVG-TOTAL BARIS PERTAMA ==========")
    if 'Avg-Total' in df.columns:
        print(f"Nilai: {df.iloc[0]['Avg-Total']}")
    else:
        print("⚠️ Kolom 'Avg-Total' TIDAK DITEMUKAN!")
    
    existing = await db.execute(
        select(OtdrResult).where(
            OtdrResult.user_id == current_user.id,
            OtdrResult.source == "sheets",
        )
    )
    for rec in existing.scalars().all():
        await db.delete(rec)
    await db.flush()
    
    saved = 0
    errors = 0
    
    for idx, row in df.iterrows():
        try:
            def g(col, default=0.0):
                try:
                    val = row.get(col, default)
                    if pd.notna(val) and val != '' and val != '-':
                        return float(val)
                    return default
                except Exception:
                    return default
            
            # 🔥 FUNGSI UNTUK AMBIL TIMESTAMP DARI KOLOM 'Time'
            def get_timestamp_from_row(row):
                """Ambil timestamp dari kolom 'Time' di sheets"""
                if 'Time' in row.index:
                    val = row.get('Time')
                    if pd.notna(val) and val != '':
                        try:
                            if isinstance(val, str):
                                # Format: 2026-06-22 08:00:00
                                if ' ' in val and '-' in val:
                                    return pd.to_datetime(val).to_pydatetime()
                                # Format: 22/06/2026 08:00
                                elif '/' in val:
                                    return pd.to_datetime(val, dayfirst=True).to_pydatetime()
                            return pd.to_datetime(val).to_pydatetime()
                        except Exception as e:
                            print(f"⚠️ ROW {idx}: Gagal parse Time '{val}': {e}")
                            return None
                return None
            
            # 🔥 AMBIL TIMESTAMP DARI KOLOM 'Time'
            timestamp = get_timestamp_from_row(row)
            if timestamp is None:
                # Fallback: pakai waktu sekarang + offset indeks (15 menit per row)
                base_time = datetime.now()
                timestamp = base_time + timedelta(minutes=idx * 15)
                print(f"⚠️ ROW {idx}: Time tidak ditemukan, pakai fallback: {timestamp}")
            
            # 🔥 AMBIL SEMUA NILAI
            prx = g('Prx (dBm)')
            d1, d2, d3, d4 = g('Distance 1'), g('Distance 2'), g('Distance 3'), g('Distance 4')
            l1, l2, l3 = g('Loss 1'), g('Loss 2'), g('Loss 3')
            l4 = None if g('Loss 4') == 0 else g('Loss 4')
            tl1, tl2, tl3, tl4 = g('Total-L 1'), g('Total-L 2'), g('Total-L 3'), g('Total-L 4')
            al1, al2, al3, al4 = g('Avg-L 1'), g('Avg-L 2'), g('Avg-L 3'), g('Avg-L 4')
            r1, r2, r3, r4 = g('Return 1'), g('Return 2'), g('Return 3'), g('Return 4')
            
            # 🔥 AMBIL AVG-TOTAL: UTAMAKAN DARI KOLOM
            avg_total = g('Avg-Total', 0.0)
            
            # 🔥 KALAU KOSONG, HITUNG MANUAL DARI Total-L4 / Distance4
            if avg_total == 0.0:
                if tl4 > 0 and d4 > 0:
                    avg_total = tl4 / d4
                    print(f"📊 ROW {idx}: Avg-Total dari kolom kosong, dihitung manual = {tl4} / {d4} = {avg_total:.3f}")
                else:
                    print(f"⚠️ ROW {idx}: Avg-Total kosong dan tidak bisa dihitung (tl4={tl4}, d4={d4})")
            else:
                print(f"✅ ROW {idx}: Avg-Total dari kolom = {avg_total:.3f}")
            
            otdr_values = {
                'Prx (dBm)': prx,
                'Distance 1': d1, 'Distance 2': d2, 'Distance 3': d3, 'Distance 4': d4,
                'Loss 1': l1, 'Loss 2': l2, 'Loss 3': l3,
                'Total-L 1': tl1, 'Total-L 2': tl2, 'Total-L 3': tl3, 'Total-L 4': tl4,
                'Avg-L 1': al1, 'Avg-L 2': al2, 'Avg-L 3': al3, 'Avg-L 4': al4,
                'Avg-Total': avg_total,
                'Return 1': r1, 'Return 2': r2, 'Return 3': r3, 'Return 4': r4,
            }
            
            pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)

            print(f"🔍 ROW {idx}: Avg-Total FINAL = {avg_total:.3f}")
            
            record = OtdrResult(
                user_id=current_user.id,
                timestamp=timestamp,  # ✅ PAKAI TIMESTAMP DARI SHEETS
                prx=prx,
                temperature=g('Temperature (C)'),
                wavelength=g('Wavelength'),
                pulse_width=g('Pulse Width (ns)'),
                distance_1=d1, distance_2=d2, distance_3=d3, distance_4=d4,
                loss_1=l1, loss_2=l2, loss_3=l3, loss_4=l4,
                total_l_1=tl1, total_l_2=tl2, total_l_3=tl3, total_l_4=tl4,
                avg_l_1=al1, avg_l_2=al2, avg_l_3=al3, avg_l_4=al4,
                avg_total=avg_total,
                return_1=r1, return_2=r2, return_3=r3, return_4=r4,
                klasifikasi=pred.get("prediction"),
                status=pred.get("status"),
                confidence=pred.get("confidence"),
                source="sheets",
            )
            db.add(record)
            saved += 1
        except Exception as e:
            logger.error(f"Sync row error: {e}")
            errors += 1
    
    await db.commit()
    return {
        "message": f"Sync selesai: {saved} baris berhasil, {errors} error",
        "saved": saved,
        "errors": errors,
        "total": len(df),
    }

@app.get("/")
async def health_check():
    return {
        "status": "online",
        "app": "OptiM API",
        "version": "2.0.1",
        "model": "loaded" if ml.lgbm_model else "not found",
    }

@app.post("/api/detect-manual")
async def detect_manual(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    # ── DETEKSI MODE MANUAL ──
    mode, cut_km = detect_manual_mode(payload)
    logger.info(f"[AUTO-DETECT] Manual mode: {mode}, cut_km: {cut_km}")

    NAN = float('nan')

    def g(key, default=0.0):
        """Ambil nilai numerik dengan default — untuk field yang SELALU harus ada (distance, prx)."""
        try:
            val = payload.get(key, default)
            if val is None or val == "":
                return default
            return float(val)
        except:
            return default

    def gn(key):
        """Ambil nilai numerik; kembalikan None jika kosong — untuk field yang boleh tidak ada."""
        val = payload.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except:
            return None

    # Distance & Prx: selalu harus ada, fallback ke 0 / default
    d1  = g('distance_1', 0.0)
    d2  = g('distance_2', 0.0)
    d3  = g('distance_3', 0.0)
    d4  = g('distance_4', 0.0)
    prx = g('prx', -15.6)

    # Loss, Total-L, Avg-L, Return: None jika tidak diisi
    # None → disimpan NULL di DB dan diubah NaN ke model oleh normalize_for_model
    l1  = gn('loss_1')
    l2  = gn('loss_2')
    l3  = gn('loss_3')
    l4  = None   # selalu None — End of Fiber, tidak pernah terukur

    tl1 = gn('total_l_1')
    tl2 = gn('total_l_2')
    tl3 = gn('total_l_3')
    tl4 = gn('total_l_4')

    al1 = gn('avg_l_1')
    al2 = gn('avg_l_2')
    al3 = gn('avg_l_3')
    al4 = gn('avg_l_4')

    avg_total = gn('avg_total')

    r1  = gn('return_1')
    r2  = gn('return_2')
    r3  = gn('return_3')
    r4  = gn('return_4')

    # ── NORMALISASI KE FORMAT MODEL ──
    parsed_data = {
        'loss_1': l1, 'loss_2': l2, 'loss_3': l3, 'loss_4': l4,
        'total_l_1': tl1, 'total_l_2': tl2, 'total_l_3': tl3, 'total_l_4': tl4,
        'avg_l_1': al1, 'avg_l_2': al2, 'avg_l_3': al3, 'avg_l_4': al4,
        'avg_total': avg_total,
        'return_1': r1, 'return_2': r2, 'return_3': r3, 'return_4': r4,
        'distance_1': d1, 'distance_2': d2, 'distance_3': d3, 'distance_4': d4,
    }
    normalized = normalize_for_model(mode, cut_km, parsed_data)

    def _nan(key, fallback):
        """
        Ambil dari normalized. Konversi None → NaN agar model menerima
        nilai yang identik dengan representasi training Fiber Cut.
        """
        val = normalized.get(key, fallback)
        if val is None:
            return NAN
        return val

    # ── OTDR VALUES UNTUK ML ──
    # Semua None dari normalized diubah ke NaN di sini.
    # NaN adalah representasi yang dipakai model saat training (bukan 0, bukan 999).
    otdr_values = {
        'Prx (dBm)' : prx,
        'Distance 1': _nan('distance_1', d1),
        'Distance 2': _nan('distance_2', d2),
        'Distance 3': _nan('distance_3', d3),
        'Distance 4': _nan('distance_4', d4),
        'Loss 1'    : _nan('loss_1',  l1),
        'Loss 2'    : _nan('loss_2',  l2),
        'Loss 3'    : _nan('loss_3',  l3),
        'Total-L 1' : _nan('total_l_1', tl1),
        'Total-L 2' : _nan('total_l_2', tl2),
        'Total-L 3' : _nan('total_l_3', tl3),
        'Total-L 4' : _nan('total_l_4', tl4),
        'Avg-L 1'   : _nan('avg_l_1',  al1),
        'Avg-L 2'   : _nan('avg_l_2',  al2),
        'Avg-L 3'   : _nan('avg_l_3',  al3),
        'Avg-L 4'   : _nan('avg_l_4',  al4),
        'Avg-Total' : _nan('avg_total', avg_total),
        'Return 1'  : _nan('return_1', r1),
        'Return 2'  : _nan('return_2', r2),
        'Return 3'  : _nan('return_3', r3),
        'Return 4'  : _nan('return_4', r4),
    }
    
    logger.info(f"[MANUAL] mode={mode}, cut_km={cut_km}")
    
    try:
        pred = ml.predict_from_otdr(otdr_values)
        logger.info(f"🤖 ML prediction SUCCESS (manual): {pred.get('prediction')}")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED (manual): {e}")
        pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
        
    user_id = current_user.id if current_user else 1
    
    try:
        def _db(key, fallback):
            """NaN dari normalize_for_model -> None -> NULL di PostgreSQL."""
            val = normalized.get(key, fallback)
            if val is None:
                return None
            if isinstance(val, float) and val != val:  # NaN check
                return None
            return val

        record = OtdrResult(
            user_id=user_id,
            timestamp=datetime.utcnow(),
            prx=prx,
            distance_1=d1,              distance_2=d2,
            distance_3=_db('distance_3', d3),  distance_4=_db('distance_4', d4),
            loss_1=_db('loss_1', l1),   loss_2=_db('loss_2', l2),
            loss_3=_db('loss_3', l3),   loss_4=None,
            total_l_1=_db('total_l_1', tl1),   total_l_2=_db('total_l_2', tl2),
            total_l_3=_db('total_l_3', tl3),   total_l_4=_db('total_l_4', tl4),
            avg_l_1=_db('avg_l_1', al1),  avg_l_2=_db('avg_l_2', al2),
            avg_l_3=_db('avg_l_3', al3),  avg_l_4=_db('avg_l_4', al4),
            avg_total=_db('avg_total', avg_total),
            return_1=_db('return_1', r1),  return_2=_db('return_2', r2),
            return_3=_db('return_3', r3),  return_4=_db('return_4', r4),
            klasifikasi=pred.get("prediction"),
            status=pred.get("status"),
            confidence=pred.get("confidence"),
            source="manual",
            raw_text=f"Manual Input (detected: {mode}, cut_km: {cut_km})",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        
        status_str = pred.get("status", "Normal")
        if status_str.lower() in ["warning", "critical"]:
            try:
                # Kirim None untuk field yang tidak ada — send_telegram_alert akan tampilkan '---'
                loss_for_alert = [
                    normalized.get('loss_1', l1),
                    normalized.get('loss_2', l2),
                    normalized.get('loss_3', l3),
                    None  # loss_4 selalu None
                ]
                rl_for_alert = [
                    normalized.get('return_1', r1),
                    normalized.get('return_2', r2),
                    normalized.get('return_3', r3),
                    normalized.get('return_4', r4),
                ]
                await asyncio.to_thread(
                    send_telegram_alert,
                    classification=pred.get("prediction"),
                    status=status_str,
                    loss=loss_for_alert,
                    rl=rl_for_alert,
                    prx=prx,
                    distances=[d1, d2, d3 if d3 else None, d4 if d4 else None],
                    timestamp=record.timestamp
                )
                record.telegram_alert_sent = True
                await db.commit()
            except Exception as tg_err:
                logger.error(f"[TELEGRAM] Error: {tg_err}")

    except Exception as e:
        logger.error(f"❌ DATABASE ERROR (manual): {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    return {
        "message": "Data manual berhasil diproses",
        "extracted": {
            "distances": [d1, d2, d3, d4],
            "losses": [l1, l2, l3, None],
            "total_ls": [tl1, tl2, tl3, tl4],
            "returns": [r1, r2, r3, r4],
            "avg_ls": [al1, al2, al3, al4],
            "avg_total": avg_total,
        },
        "prx": prx,
        "prx_source": "manual",
        "prediction": pred.get("prediction"),
        "confidence": pred.get("confidence"),
        "status": pred.get("status"),
        "id": record.id,
        "detected_mode": mode,  # ← TAMBAHKAN
        "cut_km": cut_km,       # ← TAMBAHKAN
    }

# ============================================================
# TELEGRAM DASHBOARD SLIDE UPDATE
# ============================================================

@app.post("/api/telegram-update-dashboard-slide")
async def update_dashboard_slide_data(payload: dict):
    """Update data slide dashboard terakhir"""
    global last_dashboard_slide_index, last_dashboard_slide_data
    
    try:
        record_id = payload.get('id')
        index = payload.get('index', 0)
        
        if not record_id:
            return {"error": "Missing record id"}
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(OtdrResult).where(OtdrResult.id == record_id)
            )
            record = result.scalar_one_or_none()
            
            if not record:
                return {"error": "Record not found"}
            
            last_dashboard_slide_index = index
            last_dashboard_slide_data = record
            
            logger.info(f"[TELEGRAM] Dashboard slide updated: index={index}, id={record_id}, klasifikasi={record.klasifikasi}")
            return {"status": "updated", "index": index, "id": record_id}
            
    except Exception as e:
        logger.error(f"[TELEGRAM] Error update dashboard slide: {e}")
        return {"error": str(e)}

# ============================================================
# SHARED SLIDE STATE — satu posisi untuk semua user/device
# ============================================================

@app.get("/api/shared-slide")
async def get_shared_slide():
    """Kembalikan posisi slide yang berlaku untuk semua user."""
    global shared_slide_index
    return {"current_index": shared_slide_index}

@app.post("/api/shared-slide")
async def set_shared_slide(
    payload: dict,
    current_user: User = Depends(get_current_user),
):
    """
    Update posisi slide bersama.
    Hanya dikirim oleh satu instance (master auto-play).
    Semua client lain hanya membaca lewat GET.
    """
    global shared_slide_index, slide_alert_sent_ids
    try:
        index = int(payload.get("index", 0))
        record_id = payload.get("record_id")
        shared_slide_index = index

        # Kirim alert Telegram hanya jika belum pernah dikirim untuk record ini
        if record_id and record_id not in slide_alert_sent_ids:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(OtdrResult).where(OtdrResult.id == record_id))
                record = result.scalar_one_or_none()
                if record:
                    status_str = (record.status or "").lower()
                    if status_str in ("warning", "critical"):
                        slide_alert_sent_ids.add(record_id)
                        loss_list = [record.loss_1, record.loss_2, record.loss_3, record.loss_4]
                        rl_list   = [record.return_1, record.return_2, record.return_3, record.return_4]
                        await asyncio.to_thread(
                            send_telegram_alert,
                            classification=record.klasifikasi,
                            status=record.status,
                            loss=loss_list,
                            rl=rl_list,
                            prx=record.prx,
                            distances=[record.distance_1, record.distance_2, record.distance_3, record.distance_4],
                            timestamp=record.timestamp,
                        )
                        logger.info(f"[SHARED-SLIDE] Alert sent for record {record_id} ({record.status})")

        logger.info(f"[SHARED-SLIDE] index={index}, record_id={record_id}, by user={current_user.email}")
        return {"status": "ok", "current_index": shared_slide_index}
    except Exception as e:
        logger.error(f"[SHARED-SLIDE] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# TELEGRAM BOT COMMAND HANDLER (CEK STATUS VIA TELEGRAM)
# ============================================================

async def handle_telegram_command(update: dict) -> str | None:
    """Handle command dari Telegram Bot"""
    try:
        if 'message' not in update:
            return None
            
        msg = update['message']
        text = msg.get('text', '').strip()
        chat_id = msg.get('chat', {}).get('id')
        
        if not text or not chat_id:
            return None
            
        command = text.lower().split()[0] if text else ''
        
        # /status - cek status terakhir
        if command == '/status':
            return await get_telegram_status()
        
        # /rekap - rekap hari ini
        elif command == '/rekap':
            return await get_telegram_rekap()
        
        # /help atau /start - bantuan
        elif command in ['/help', '/start']:
            return get_telegram_help()
        
        # Keyword natural: "status", "cek", "kondisi", "latest"
        elif any(kw in text.lower() for kw in ['status', 'cek', 'kondisi', 'bagaimana', 'latest', 'terakhir']):
            return await get_telegram_status()
        
        return None
        
    except Exception as e:
        logger.error(f"[TELEGRAM] Error handling command: {e}")
        return None

async def get_telegram_status() -> str:
    """Ambil status dari DASHBOARD slide"""
    global last_dashboard_slide_index, last_dashboard_slide_data
    
    try:
        # 🔥 CEK APAKAH ADA DATA SLIDE
        if last_dashboard_slide_data is None:
            return "📡 Belum ada data slide. Buka Dashboard dulu ya!"
        
        # 🔥 LANGSUNG PAKAI DATA DARI SLIDE
        record = last_dashboard_slide_data
        slide_info = f" (Dashboard Slide #{last_dashboard_slide_index + 1})"
        source_label = {
            'ocr': '📷 OCR',
            'manual': '✏️ Manual',
            'sheets': '📊 Sheets'
        }.get(record.source, '📡 Unknown')
        
        # Ambil rekap hari ini (tetap dari database)
        async with AsyncSessionLocal() as db:
            result_today = await db.execute(
                select(OtdrResult)
                .where(OtdrResult.timestamp >= datetime.utcnow() - timedelta(days=1))
            )
            today_records = result_today.scalars().all()
            
            today_total = len(today_records)
            today_faults = sum(1 for r in today_records if r.klasifikasi != "Normal")
            
            # Status emoji
            status_emoji = "🟢" if record.status == "Normal" else "🟡" if record.status == "Warning" else "🔴"
            
            # Loss values
            loss_values = [record.loss_1, record.loss_2, record.loss_3, record.loss_4]
            loss_str = " | ".join([f"{v:.2f} dB" if v else "---" for v in loss_values])
            
            # Return loss
            return_values = [record.return_1, record.return_2, record.return_3, record.return_4]
            return_str = " | ".join([f"{v:.1f} dB" if v else "---" for v in return_values])
            
            # Waktu
            local_time = record.timestamp + timedelta(hours=7) if record.timestamp else datetime.utcnow() + timedelta(hours=7)
            time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # Status hari ini
            today_status = "🟢 Normal" if today_faults == 0 else "🟡 Ada gangguan" if today_faults < 3 else "🔴 Banyak gangguan"
            
            message = f"""
{status_emoji} <b>STATUS DARI DASHBOARD</b> {status_emoji}{slide_info}
<i>Sumber: {source_label}</i>

<b>📊 Pengukuran:</b>
• <b>Waktu:</b> {time_str}
• <b>Klasifikasi:</b> {record.klasifikasi}
• <b>Status:</b> {record.status}
• <b>PRX:</b> {record.prx:.2f} dBm
• <b>Confidence:</b> {record.confidence:.1f}%

<b>📈 Loss per KM:</b>
{loss_str}

<b>🔄 Return Loss per KM:</b>
{return_str}

<b>📋 Rekap Hari Ini:</b>
• Total Pengukuran: <b>{today_total}</b>
• Gangguan: <b>{today_faults}</b>
• Status: {today_status}

<b>💡 Perintah:</b>
/status - Cek status dari Dashboard
/rekap - Rekap hari ini
/help - Bantuan
"""
            return message
            
    except Exception as e:
        logger.error(f"[TELEGRAM] Error get status from dashboard: {e}")
        return f"❌ Gagal mengambil data dashboard. Error: {str(e)}"

async def get_telegram_rekap() -> str:
    """Rekap gangguan hari ini"""
    try:
        async with AsyncSessionLocal() as db:
            now_wib = datetime.utcnow() + timedelta(hours=7)
            today_start = datetime(now_wib.year, now_wib.month, now_wib.day) - timedelta(hours=7)
            
            result = await db.execute(
                select(OtdrResult)
                .where(OtdrResult.timestamp >= today_start)
                .order_by(OtdrResult.timestamp.desc())
            )
            records = result.scalars().all()
            
            if not records:
                return "📡 Belum ada pengukuran hari ini."
            
            total = len(records)
            normal = sum(1 for r in records if r.klasifikasi == "Normal")
            faults = total - normal
            
            from collections import Counter
            classification_counts = Counter(r.klasifikasi for r in records if r.klasifikasi != "Normal")
            
            detail_lines = []
            for klasifikasi, count in classification_counts.most_common():
                emoji = {
                    "Fiber Cut": "🔴",
                    "Nearly Cut": "🟠",
                    "Bending": "🟡",
                    "Dirty Connector": "🟡",
                    "Bad Splice": "🟡",
                    "Air Gap": "🟡",
                }.get(klasifikasi, "🟡")
                detail_lines.append(f"• {emoji} {klasifikasi}: {count} kali")
            
            detail_str = "\n".join(detail_lines) if detail_lines else "✅ Tidak ada gangguan"
            
            status_jaringan = "🟢 SEHAT" if faults == 0 else "🟡 PERLU PERHATIAN" if faults < 3 else "🔴 KRITIS"
            
            message = f"""
📊 <b>REKAP HARI INI</b> 📊

<b>Total Pengukuran:</b> {total}
<b>Normal:</b> {normal} 🟢
<b>Gangguan:</b> {faults} {"🔴" if faults > 0 else "✅"}

<b>📋 Rincian Gangguan:</b>
{detail_str}

<b>📈 Statistik:</b>
• Rasio Gangguan: {(faults/total*100):.1f}%
• Status Jaringan: {status_jaringan}

💡 Ketik <b>/status</b> untuk cek data terakhir
"""
            return message
            
    except Exception as e:
        logger.error(f"[TELEGRAM] Error get rekap: {e}")
        return "❌ Gagal mengambil rekap. Coba lagi nanti."

def get_telegram_help() -> str:
    """Bantuan Telegram Bot"""
    return """
🤖 <b>OptiM Bot - Panduan Perintah</b> 🤖

<b>📋 Perintah yang tersedia:</b>

• <b>/status</b> - Cek status dari Dashboard (slide yang sedang ditampilkan)
• <b>/rekap</b> - Lihat rekap gangguan hari ini
• <b>/help</b> - Tampilkan bantuan ini

<b>💬 Chat Natural:</b>
Anda juga bisa bertanya langsung:
• "status terakhir" 
• "cek status"
• "kondisi hari ini"

<b>🔔 Alert Otomatis:</b>
Bot akan mengirim notifikasi saat terdeteksi:
• <b>Warning</b> 🟡 - Gangguan ringan
• <b>Critical</b> 🔴 - Gangguan serius

<b>📱 Gunakan perintah di atas untuk monitoring cepat!</b>
"""

def send_telegram_message(chat_id: str, message: str):
    """Kirim pesan biasa ke Telegram (bukan alert)"""
    if not TELEGRAM_BOT_TOKEN:
        logger.info("[TELEGRAM] Bot token belum dikonfigurasi")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"[TELEGRAM] Pesan berhasil dikirim ke {chat_id}")
        else:
            logger.error(f"[TELEGRAM] Gagal kirim pesan: {response.text}")
    except Exception as e:
        logger.error(f"[TELEGRAM] Error: {e}")

@app.post("/api/telegram-webhook")
async def telegram_webhook(request: dict):
    """Webhook untuk menerima command dari Telegram"""
    logger.info(f"[TELEGRAM] Webhook received: {request}")
    
    try:
        response_text = await handle_telegram_command(request)
        
        if response_text:
            chat_id = request.get('message', {}).get('chat', {}).get('id')
            if chat_id:
                send_telegram_message(str(chat_id), response_text)
                return {"ok": True}
        
        return {"ok": True}
        
    except Exception as e:
        logger.error(f"[TELEGRAM] Webhook error: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/telegram-setup")
async def setup_telegram_webhook():
    """Setup webhook untuk Telegram Bot"""
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "TELEGRAM_BOT_TOKEN not configured"}
    
    webhook_url = "https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net/api/telegram-webhook"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {"url": webhook_url}
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# DASHBOARD - PROCESS SOR EXCEL FILE (RANDOM FOREST)
# ============================================================

@app.post("/api/dashboard/process-sor")
async def process_sor_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Proses file SOR (CSV atau Excel) dengan sliding window dan Random Forest.
    """
    # ── LOG: MAIN START ──
    logger.info("=" * 70)
    logger.info("[SOR] ▶ ENTER PROCESS_SOR")
    logger.info(f"[SOR]   user={current_user.email} (id={current_user.id})")
    logger.info(f"[SOR]   filename={file.filename}, content_type={file.content_type}")

    # ── LOG: AUTH SUCCESS ──
    logger.info("[SOR] ✅ AUTH SUCCESS — token valid, user ditemukan")

    # 1. Validasi file (support CSV + Excel)
    ALLOWED_EXTENSIONS = ('.xlsx', '.xls', '.csv')
    if not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        logger.warning(f"[SOR] ❌ FILE REJECTED: {file.filename}")
        raise HTTPException(
            status_code=400, 
            detail=f"File harus berformat: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # 2. Baca file (CSV atau Excel)
    logger.info("[SOR] ── STEP 1: READ FILE ──")
    try:
        content = await file.read()
        logger.info(f"[SOR]   file size = {len(content)} bytes")
        
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))
            
        logger.info(f"[SOR] ✅ READ EXCEL/CSV: {len(df)} rows × {len(df.columns)} columns")
        logger.info(f"[SOR]   columns = {df.columns.tolist()}")

    except Exception as e:
        logger.error(f"[SOR] ❌ READ FILE FAILED: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Gagal membaca file: {str(e)}")
    
    # 3. Cari kolom Backscatter
    logger.info("[SOR] ── STEP 2: FIND BACKSCATTER COLUMN ──")
    backscatter_col = None
    possible_cols = ['backscatter (db)', 'backscatter', 'db', 'scatter', 'amplitude']
    for col in df.columns:
        col_lower = col.lower().strip()
        for possible in possible_cols:
            if possible in col_lower:
                backscatter_col = col
                break
        if backscatter_col:
            break
    
    if backscatter_col is None:
        logger.error(f"[SOR] ❌ BACKSCATTER COLUMN NOT FOUND. Available: {df.columns.tolist()}")
        raise HTTPException(
            status_code=400, 
            detail=f"Kolom 'Backscatter (dB)' tidak ditemukan. Kolom tersedia: {', '.join(df.columns.tolist())}"
        )
    
    logger.info(f"[SOR] ✅ BACKSCATTER COLUMN FOUND: '{backscatter_col}'")

    # 4. Ambil data Backscatter
    backscatter_data = pd.to_numeric(df[backscatter_col], errors='coerce').dropna().values.tolist()
    logger.info(f"[SOR]   total data points = {len(backscatter_data)}")
    
    if len(backscatter_data) < 128:
        raise HTTPException(
            status_code=400,
            detail=f"Data Backscatter hanya {len(backscatter_data)} titik. Minimal 128 titik diperlukan."
        )
    
    # 5. Cek model SOR sudah loaded
    logger.info("[SOR] ── STEP 3: CHECK MODEL ──")
    if ml_sor.sor_model is None:
        logger.error("[SOR] ❌ SOR MODEL IS NONE — model belum dimuat!")
        logger.error(f"[SOR]   SOR_MODEL_PATHS dicek: {[str(p) for p in ml_sor.SOR_MODEL_PATHS]}")
        raise HTTPException(
            status_code=500,
            detail="Model Random Forest belum dimuat. Pastikan file model ada di folder models/sor/"
        )
    logger.info(f"[SOR] ✅ MODEL READY: {type(ml_sor.sor_model).__name__}")
    logger.info(f"[SOR]   scaler loaded = {ml_sor.sor_scaler is not None}")
    logger.info(f"[SOR]   label_classes = {ml_sor.sor_label_classes}")

    # 6. BATCH PREDICT — window_size=34, stride=6 (sesuai parameter training model)
    window_size  = 34
    stride       = 6
    total_windows = max(0, (len(backscatter_data) - window_size) // stride + 1)

    logger.info(f"[SOR] ── STEP 4: BATCH PREDICT ──")
    logger.info(f"[SOR]   window_size={window_size}, stride={stride}, total_windows={total_windows}")

    try:
        predictions = await asyncio.to_thread(
            ml_sor.predict_sor_batch,
            backscatter_data,
            window_size,
            stride,
        )
    except Exception as e:
        logger.error(f"[SOR] ❌ BATCH PREDICT FAILED: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Prediksi gagal: {str(e)}")

    logger.info(f"[SOR] ✅ PREDICTION SUCCESS: {len(predictions)} windows selesai")

    # 7. Hitung klasifikasi final dari kelas terbanyak
    from collections import Counter
    class_counts: dict = Counter(p["prediction"] for p in predictions)
    final_class = class_counts.most_common(1)[0][0] if class_counts else "Normal"

    cls_lower = final_class.lower()
    if cls_lower == "normal":
        final_status = "Normal"
    elif any(k in cls_lower for k in ["cut", "nearly"]):
        final_status = "Critical"
    else:
        final_status = "Warning"

    logger.info(f"[SOR]   final_class={final_class}, final_status={final_status}")

    # 8. Simpan ke database
    logger.info("[SOR] ── STEP 5: SAVE TO DB ──")
    try:
        db_record = DashboardResult(
            user_id       = current_user.id,
            filename      = file.filename,
            total_points  = len(backscatter_data),
            total_windows = len(predictions),
            classification= final_class,
            status        = final_status,
        )
        db.add(db_record)
        await db.commit()
        await db.refresh(db_record)
        logger.info(f"[SOR] ✅ SAVED TO DB: id={db_record.id}, class={final_class}, status={final_status}")
    except Exception as e:
        logger.error(f"[SOR] ❌ DB SAVE FAILED: {e}", exc_info=True)

    # 9. Kirim Telegram jika Warning/Critical
    logger.info(f"[SOR] ── STEP 6: TELEGRAM — status={final_status} ──")
    try:
        if final_status != "Normal":
            from app.schemas import DashboardResultResponse
            send_telegram_dashboard(final_class, final_status)
    except Exception as e:
        logger.error(f"[SOR] ❌ TELEGRAM FAILED: {e}", exc_info=True)

    logger.info("[SOR] ── RETURN RESPONSE ──")
    logger.info("=" * 70)

    # Sanitize NaN/inf → None agar JSON serializable
    def sanitize(val):
        if val is None:
            return None
        try:
            f = float(val)
            if f != f or f == float('inf') or f == float('-inf'):
                return None
            return f
        except Exception:
            return None

    clean_backscatter = [sanitize(v) for v in backscatter_data]

    return {
        "success"      : True,
        "backscatter"  : clean_backscatter,
        "distance"     : [],
        "predictions"  : predictions,
        "total_windows": len(predictions),
        "window_size"  : window_size,
        "stride"       : stride,
        "total_points" : len(backscatter_data),
        "filename"     : file.filename,
        "classification": final_class,
        "status"       : final_status,
        "metadata": {
            "columns": df.columns.tolist(),
            "rows"   : len(df),
        }
    }

# ── Dashboard: Classification History ──────────────────────────────────────────

@app.get("/api/dashboard/history", response_model=list[DashboardResultResponse])
async def get_dashboard_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kembalikan history klasifikasi SOR milik user, terbaru di atas."""
    result = await db.execute(
        select(DashboardResult)
        .where(DashboardResult.user_id == current_user.id)
        .order_by(DashboardResult.created_at.desc())
    )
    return result.scalars().all()


@app.delete("/api/dashboard/history/{history_id}")
async def delete_dashboard_history(
    history_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hapus satu history klasifikasi Dashboard milik user."""
    result = await db.execute(
        select(DashboardResult).where(
            DashboardResult.id == history_id,
            DashboardResult.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="History tidak ditemukan")
    await db.delete(record)
    await db.commit()
    logger.info(f"[DASHBOARD] Deleted history id={history_id} by user={current_user.email}")
    return {"success": True, "message": f"History id={history_id} berhasil dihapus"}