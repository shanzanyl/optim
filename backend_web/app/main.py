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

from PIL import Image
import pytesseract
import cv2
import requests
import tempfile

from app.database import Base, engine, get_db, AsyncSessionLocal
from app.models import User, OtdrResult
from app.schemas import UserRegister, UserLogin, TokenResponse, UserOut, ManualClassifyRequest
from app.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, get_optional_user, get_current_admin
)
from app.parseotdr import parse_otdr_table, extract_prx
from app import ml

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

def send_telegram_alert(classification: str, status: str, loss: list, rl: list, prx, distances: list = None, timestamp = None):
    """Mengirim pesan notifikasi gangguan ke Telegram Teknisi dengan format detail baru"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info("[TELEGRAM] Notifikasi dibatalkan karena token/chat_id belum dikonfigurasi di .env")
        return

    status_lower = status.lower()
    if status_lower not in ["warning", "critical"]:
        logger.info(f"[TELEGRAM] Status '{status}' Normal, alert tidak dikirim.")
        return

    # Format values safely
    prx_val = round(float(prx), 2) if prx is not None else 0.0
    safe_loss = [float(l) if l is not None else 0.0 for l in loss]
    safe_rl = [float(r) if r is not None else 0.0 for r in rl]
    
    while len(safe_loss) < 4:
        safe_loss.append(0.0)
    while len(safe_rl) < 4:
        safe_rl.append(0.0)

    if not distances:
        distances = [1.004, 2.006, 3.010, 4.014]
    
    safe_dist = []
    for i in range(4):
        if i < len(distances) and distances[i] is not None:
            safe_dist.append(float(distances[i]))
        else:
            safe_dist.append(float(i + 1))
            
    classification_lower = classification.lower() if classification else ""
    is_fiber_cut = "cut" in classification_lower or "putus" in classification_lower
    
    if is_fiber_cut:
        cut_idx = -1
        for idx, val in enumerate(safe_loss):
            if val == 0.0:
                cut_idx = idx
                break
        if cut_idx == -1:
            cut_idx = 3
        km_loc = cut_idx + 1
        jarak_loc = round(safe_dist[cut_idx], 3)
        redaman_loc = 0.0
    else:
        max_loss_val = max(safe_loss)
        max_loss_idx = safe_loss.index(max_loss_val) if safe_loss else 0
        km_loc = max_loss_idx + 1
        jarak_loc = round(safe_dist[max_loss_idx], 3)
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
    loss_km4_str = "---" if safe_loss[3] == 0.0 else f"{safe_loss[3]:.2f} dB"
    
    message = (
        f"🚨 <b>GANGGUAN TERDETEKSI!</b> 🚨\n\n"
        f"<b>Jenis Gangguan:</b> {classification}\n"
        f"<b>Tingkat Bahaya:</b> {status_cap}\n\n"
        f"<b>Parameter Pengukuran:</b>\n"
        f"• <b>Daya (Prx):</b> {prx_val} dBm\n\n"
        f"<b>Detail Redaman &amp; Pantulan:</b>\n"
        f"• <b>KM 1:</b> Loss {safe_loss[0]:.2f} dB | Return {safe_rl[0]:.2f} dB\n"
        f"• <b>KM 2:</b> Loss {safe_loss[1]:.2f} dB | Return {safe_rl[1]:.2f} dB\n"
        f"• <b>KM 3:</b> Loss {safe_loss[2]:.2f} dB | Return {safe_rl[2]:.2f} dB\n"
        f"• <b>KM 4:</b> Loss {loss_km4_str} | Return {safe_rl[3]:.2f} dB\n\n"
        f"<b>Lokasi Gangguan:</b> KM {km_loc} (Jarak: {jarak_loc:.3f} km, Redaman: {redaman_loc:.2f} dB)\n"
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
    
    for key in result:
        if isinstance(result[key], float):
            result[key] = round(result[key], 2)
    
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
        "https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net",
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_FOLDER", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# OCR PREPROCESSING (DIPERBAIKI)
# ═══════════════════════════════════════════════════════════════════

def preprocess_image_simple(image_bytes: bytes) -> list:
    """Preprocessing lebih agresif untuk OCR"""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    
    results = []
    
    y_start = int(h * 0.25)
    y_end = int(h * 0.98)
    cropped = img[y_start:y_end, 0:w]
    resized = cv2.resize(cropped, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4,4))
    enhanced = clahe.apply(gray)
    denoised = cv2.fastNlMeansDenoising(enhanced, h=30)
    binary = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                    cv2.THRESH_BINARY, 15, 8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    
    results.append(Image.fromarray(cleaned))
    results.append(Image.fromarray(cv2.bitwise_not(cleaned)))
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

        configs = [
            "--oem 3 --psm 6",
            "--oem 3 --psm 4",
            "--oem 3 --psm 11",
            "--oem 1 --psm 6",
            "--oem 3 --psm 3",
        ]

        for img in images:
            for config in configs:
                try:
                    text = pytesseract.image_to_string(img, config=config)
                    decimal_score = len(re.findall(r'\d+\.\d{4,}', text))
                    loss_score = len(re.findall(r'0\.\d{2}', text))
                    total_score = decimal_score + loss_score * 2

                    if total_score > best_score:
                        best_score = total_score
                        best_text = text
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
    OCR_SPACE_API_KEY = "helloworld"
    
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
            timeout=15,
        )
        result = response.json()
        if result.get('ParsedResults') and len(result['ParsedResults']) > 0:
            text = result['ParsedResults'][0]['ParsedText']
            logger.info(f"OCR.space extracted {len(text)} chars")
            return text
        return ""
    except Exception as e:
        logger.error(f"OCR.space error: {e}")
        return ""

# ═══════════════════════════════════════════════════════════════════
# OTDR PARSER (DIPERBAIKI)
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_table_simple(raw_text: str) -> tuple[list, float]:
    """
    Parse teks OCR OTDR secara global dan dinamis.
    Versi improved dengan anchor-based detection.
    """
    text = raw_text.replace(',', '.')
    
    # 1. Tokenisasi teks menjadi token numerik
    raw_tokens = []
    for t in text.replace('\t', ' ').split():
        # Lewati token yang berisi huruf alfabet murni
        t_alpha = re.sub(r'[^a-zA-Z\u0400-\u04FF]', '', t)
        if t_alpha and t_alpha.isalpha() and t_alpha not in ('dB', 'km'):
            continue
            
        t_clean = t.replace('km', '').replace('dB', '').replace('/km', '').strip()
        if re.match(r'^[-–—]+$', t_clean) or t_clean == '':
            raw_tokens.append('---')
        else:
            t_clean2 = re.sub(r'[^\d\.\-]', '', t_clean)
            if t_clean2 and t_clean2 not in ('-', '.'):
                try:
                    raw_tokens.append(float(t_clean2))
                except ValueError:
                    pass
    
    logger.info(f"Raw numeric/dash tokens parsed: {raw_tokens[:30]}")
    
    # 2. Cari posisi indeks anchor distance (KM1, KM2, KM3, KM4)
    anchors = {}
    last_idx = -1
    for i in range(1, 5):
        best_idx = -1
        for idx in range(last_idx + 1, len(raw_tokens)):
            val = raw_tokens[idx]
            if isinstance(val, float) and (i - 0.25 <= val <= i + 0.25):
                best_idx = idx
                break
        if best_idx != -1:
            anchors[i] = best_idx
            last_idx = best_idx
    
    # Taksir posisi anchor yang hilang
    for i in range(1, 5):
        if i not in anchors:
            if i - 1 in anchors:
                anchors[i] = min(anchors[i-1] + 6, len(raw_tokens) - 1)
            elif i + 1 in anchors:
                anchors[i] = max(anchors[i+1] - 6, 0)
            else:
                anchors[i] = min((i - 1) * 6, len(raw_tokens) - 1)
    
    logger.info(f"Distance anchors: {anchors}")
    
    # 3. Slicing token berdasarkan anchor
    slices = {}
    sorted_anchors = sorted(anchors.items())
    for idx_item, (i, start_idx) in enumerate(sorted_anchors):
        end_idx = len(raw_tokens)
        if idx_item + 1 < len(sorted_anchors):
            end_idx = sorted_anchors[idx_item + 1][1]
        slices[i] = raw_tokens[start_idx:end_idx]
    
    # 4. Klasifikasi field per baris
    rows = []
    for i in range(1, 5):
        row_tokens = list(slices.get(i, []))
        if not row_tokens:
            rows.append({
                'distance': float(i),
                'loss': None if i == 4 else 0.0,
                'total_l': 0.0,
                'avg_l': 0.0,
                'return': -45.0
            })
            continue
        
        # Token pertama adalah distance
        dist = row_tokens[0]
        
        # Ekstrak return loss (angka antara 25-65)
        ret = -45.0
        ret_idx = -1
        for idx, val in enumerate(row_tokens):
            if isinstance(val, float) and (25.0 <= abs(val) <= 65.0):
                ret = -abs(val)
                ret_idx = idx
                break
        if ret_idx != -1:
            row_tokens.pop(ret_idx)
        
        # Ekstrak section (nilai sekitar 1.0)
        sect = 1.0
        sect_idx = -1
        for idx, val in enumerate(row_tokens[1:], start=1):
            if isinstance(val, float) and 0.8 <= val <= 1.2:
                sect = val
                sect_idx = idx
                break
        if sect_idx != -1:
            row_tokens.pop(sect_idx)
        
        # Hapus token distance
        row_tokens.pop(0)
        
        # Sisa token dipetakan ke loss, total_l, avg_l
        remaining = [v for v in row_tokens if isinstance(v, float) or v == '---']
        
        if i == 4:  # KM4 khusus
            loss = None
            # Cari total_l dan avg_l dari remaining
            pos_vals = [v for v in remaining if isinstance(v, float) and v > 0]
            if len(pos_vals) >= 2:
                total_l = pos_vals[0]
                avg_l = pos_vals[1]
            elif len(pos_vals) == 1:
                total_l = pos_vals[0]
                avg_l = 0.0
            else:
                total_l = 0.0
                avg_l = 0.0
        else:
            # KM1, KM2, KM3
            if len(remaining) >= 3:
                loss = remaining[0] if isinstance(remaining[0], float) else 0.0
                total_l = remaining[1] if isinstance(remaining[1], float) else 0.0
                avg_l = remaining[2] if isinstance(remaining[2], float) else 0.0
            elif len(remaining) == 2:
                # Kasus: loss dan total_l aja, avg_l dihitung
                loss = remaining[0] if isinstance(remaining[0], float) else 0.0
                total_l = remaining[1] if isinstance(remaining[1], float) else 0.0
                avg_l = total_l / dist if dist > 0 else 0.0
            elif len(remaining) == 1:
                # Kasus: cuma total_l
                loss = 0.0
                total_l = remaining[0] if isinstance(remaining[0], float) else 0.0
                avg_l = total_l / dist if dist > 0 else 0.0
            else:
                loss = 0.0
                total_l = 0.0
                avg_l = 0.0
        
        # Format row data
        row_data = {
            'distance': round(float(dist), 5),
            'loss': round(float(loss), 3) if loss is not None and loss != '---' else (0.0 if i != 4 else 0.0),
            'total_l': round(float(total_l), 3) if isinstance(total_l, float) else 0.0,
            'avg_l': round(float(avg_l), 3) if isinstance(avg_l, float) else 0.0,
            'return': round(float(ret), 2)
        }
        rows.append(row_data)
    
    # 5. Hitung avg_total dari header
    avg_total = 0.0
    match_avg = re.search(r'(\d+\.\d{2,})\s*dB/km', text)
    if match_avg:
        avg_total = float(match_avg.group(1))
    
    # 6. Normalisasi: pastikan 4 baris
    while len(rows) < 4:
        rows.append({
            'distance': float(len(rows) + 1),
            'loss': 0.0,
            'total_l': 0.0,
            'avg_l': 0.0,
            'return': -45.0
        })
    
    # Log hasil
    logger.info("===== FINAL PARSED ROWS =====")
    for i, row in enumerate(rows):
        logger.info(f"  KM{i+1}: dist={row['distance']}, loss={row['loss']}, total_l={row['total_l']}, avg_l={row['avg_l']}, return={row['return']}")
    
    logger.info(f"AVG TOTAL = {avg_total}")
    
    return rows, avg_total

# ═══════════════════════════════════════════════════════════════════
# GEMINI AI CHATBOT
# ═══════════════════════════════════════════════════════════════════

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = None
gemini_model_name = None

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]

try:
    import google.genai as genai_new # type: ignore
    if GEMINI_API_KEY:
        gemini_client = genai_new.Client(api_key=GEMINI_API_KEY)
        gemini_model_name = "gemini-2.5-flash"
        logger.info(f"✅ Gemini AI configured: {gemini_model_name}")
except ImportError:
    try:
        import google.generativeai as genai_old # type: ignore
        if GEMINI_API_KEY:
            genai_old.configure(api_key=GEMINI_API_KEY)
            gemini_client = genai_old.GenerativeModel(model_name="gemini-2.5-flash")
            gemini_model_name = "legacy"
            logger.info("✅ Gemini AI configured via legacy")
    except Exception as e:
        logger.warning(f"⚠️ Gagal mengonfigurasi Gemini AI: {e}")
except Exception as e:
    logger.warning(f"⚠️ Gagal mengonfigurasi Gemini AI: {e}")

SYSTEM_INSTRUCTION = (
    "Anda adalah OptiM AI Assistant, asisten chatbot profesional dan ramah untuk website OptiM "
    "(sistem monitoring & klasifikasi gangguan serat optik). Anda memiliki akses ke DATA REAL-TIME dari database monitoring.\n\n"
    "KEMAMPUAN ANDA:\n"
    "1. Menjawab pertanyaan seputar fiber optik, jenis gangguan, dan cara membaca parameter OTDR.\n"
    "2. Merekap data monitoring: total pengukuran, jumlah dan jenis gangguan.\n"
    "3. Memberikan analisis tren gangguan dan rekomendasi teknis.\n\n"
    "ATURAN FORMAT JAWABAN:\n"
    "1. Jawablah dalam bahasa Indonesia yang sopan, RINGKAS, INFORMATIF.\n"
    "2. Gunakan tag HTML dasar langsung (<strong>, <br>, <ul>, <li>).\n"
    "3. Jika tidak ada gangguan, sampaikan dengan jelas."
)

BOT_RESPONSES = {
    "normal": "Kondisi <strong>Normal</strong> pada jaringan fiber optik ditandai dengan redaman (loss) transmisi yang sangat rendah, biasanya di bawah 0.22 dB/km.",
    "bending": "<strong>Bending (Tekukan Makro)</strong> terjadi ketika kabel fiber optik tertekuk melebihi radius kelengkungan minimumnya.<br><br><strong>Solusi:</strong> Periksa jalur kabel fisik, rapihkan tumpukan serat di dalam OTB.",
    "dirty": "<strong>Dirty Connector (Konektor Kotor)</strong> adalah masalah paling umum.<br><br><strong>Solusi:</strong> Bersihkan ferrule konektor menggunakan Fiber Cleaning Cassette.",
    "cut": "<strong>Fiber Cut (Kabel Putus)</strong> adalah gangguan kritis.<br><br><strong>Solusi:</strong> Lakukan pengukuran OTDR untuk mencari titik putus, lalu sambung ulang.",
    "splice": "<strong>Bad Splice (Penyambungan Buruk)</strong> terjadi saat proses fusion splicing tidak sempurna.<br><br><strong>Solusi:</strong> Potong kembali dan lakukan splicing ulang.",
    "gap": "<strong>Air Gap (Celah Udara)</strong> terjadi ketika terdapat ruang udara di antara konektor.<br><br><strong>Solusi:</strong> Pastikan konektor terkunci rapat.",
    "nearly": "<strong>Nearly Cut (Hampir Putus)</strong> adalah kondisi kritis.<br><br><strong>Solusi:</strong> Segera jadwalkan pemeliharaan preventif.",
    "bantuan": "Saya dapat membantu Anda memberikan informasi tentang jaringan fiber optik. Silakan ketik nama gangguan: Bending, Dirty Connector, Fiber Cut, Bad Splice, Air Gap, Nearly Cut.",
    "default": "Maaf, saya tidak begitu memahami pertanyaan tersebut. Coba tanyakan mengenai jenis gangguan jaringan fiber optik."
}

def get_local_chatbot_response(query: str) -> str:
    clean_query = query.lower()
    if "bending" in clean_query or "tekuk" in clean_query:
        return BOT_RESPONSES["bending"]
    elif "dirty" in clean_query or "kotor" in clean_query or "konektor" in clean_query:
        return BOT_RESPONSES["dirty"]
    elif "fiber cut" in clean_query or "putus" in clean_query or "cut" in clean_query:
        return BOT_RESPONSES["cut"]
    elif "splice" in clean_query or "sambung" in clean_query:
        return BOT_RESPONSES["splice"]
    elif "gap" in clean_query or "celah" in clean_query or "air gap" in clean_query:
        return BOT_RESPONSES["gap"]
    elif "nearly" in clean_query or "hampir" in clean_query:
        return BOT_RESPONSES["nearly"]
    elif "normal" in clean_query or "sehat" in clean_query:
        return BOT_RESPONSES["normal"]
    elif "bantuan" in clean_query or "tolong" in clean_query or "halo" in clean_query or "hai" in clean_query:
        return BOT_RESPONSES["bantuan"]
    return BOT_RESPONSES["default"]

def format_markdown_to_html(text: str) -> str:
    text = re.sub(r'```html\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)
    html = html.replace('\n', '<br>')
    html = re.sub(r'(<ul>|<ol>|<li>)\s*<br>', r'\1', html)
    html = re.sub(r'<br>\s*(</ul>|</ol>|</li>)', r'\1', html)
    html = re.sub(r'</li>\s*<br>\s*<li>', r'</li><li>', html)
    html = re.sub(r'</ul>\s*<br>\s*<ul>', r'</ul><ul>', html)
    html = re.sub(r'(<br>\s*){2,}', r'<br>', html)
    return html.strip()

def make_db_aware_response(user_message: str, today_total: int, today_g: int, today_detail: str,
                            week_total: int, week_g: int, week_detail: str,
                            month_total: int, month_g: int, month_detail: str,
                            total_data: int, all_g_total: int, all_detail: str,
                            latest, daily_records: list = None, history_records: list = None) -> str:
    q = user_message.lower()
    
    # Rekap hari ini
    if any(x in q for x in ["hari ini", "today", "hari", "sekarang"]):
        return (
            f"<strong>Rekap Monitoring Hari Ini</strong><br>"
            f"Total Pengukuran: <strong>{today_total}</strong><br>"
            f"Total Gangguan: <strong>{today_g}</strong><br>"
            f"Rincian: {today_detail if today_detail else 'Tidak ada gangguan'}"
        )
    # Rekap 7 hari
    elif any(x in q for x in ["minggu", "7 hari", "seminggu", "week"]):
        return (
            f"<strong>Rekap Monitoring 7 Hari Terakhir</strong><br>"
            f"Total Pengukuran: <strong>{week_total}</strong><br>"
            f"Total Gangguan: <strong>{week_g}</strong><br>"
            f"Rincian: {week_detail if week_detail else 'Tidak ada gangguan'}"
        )
    # Rekap 30 hari
    elif any(x in q for x in ["bulan", "30 hari", "sebulan", "month"]):
        return (
            f"<strong>Rekap Monitoring Bulan Ini (30 Hari Terakhir)</strong><br>"
            f"Total Pengukuran: <strong>{month_total}</strong><br>"
            f"Total Gangguan: <strong>{month_g}</strong><br>"
            f"Rincian: {month_detail if month_detail else 'Tidak ada gangguan'}"
        )
    # Rekap keseluruhan
    elif any(x in q for x in ["semua", "keseluruhan", "total", "rekap", "laporan", "berapa", "gangguan"]):
        return (
            f"<strong>Rekap Keseluruhan Sistem OptiM</strong><br>"
            f"Total Seluruh Pengukuran: <strong>{total_data}</strong><br>"
            f"Total Seluruh Gangguan: <strong>{all_g_total}</strong><br>"
            f"Rincian: {all_detail if all_detail else 'Tidak ada gangguan'}"
        )
    else:
        return get_local_chatbot_response(user_message)

# ═══════════════════════════════════════════════════════════════════
# LOCAL KNOWLEDGE BASE (FALLBACK UNTUK CHATBOT)
# ═══════════════════════════════════════════════════════════════════

LOCAL_KNOWLEDGE = {
    "loss": {
        "keywords": ["loss", "redaman", "attenuation", "loss km"],
        "response": """
            <strong>Apa itu Loss (Redaman)?</strong><br><br>
            Loss atau redaman adalah <strong>penurunan kekuatan sinyal</strong> saat melewati serat optik. 
            Diukur dalam satuan <strong>dB (decibel)</strong>.<br><br>
            <strong>Nilai Normal:</strong><br>
            • < 0.25 dB/km = Sangat Baik ✅<br>
            • 0.25 - 0.35 dB/km = Normal 🟡<br>
            • > 0.35 dB/km = Bermasalah 🔴<br><br>
            <strong>Penyebab Loss Tinggi:</strong><br>
            • Konektor kotor<br>
            • Tekukan kabel<br>
            • Sambungan buruk<br>
            • Kabel putus (Fiber Cut)<br><br>
            <strong>Cara Mengecek di OptiM:</strong><br>
            Lihat grafik <strong>"Loss per KM"</strong> di Dashboard. 
            Jika melebihi garis merah (threshold 1.2 dB), itu pertanda gangguan.
        """
    },
    "return_loss": {
        "keywords": ["return loss", "orl", "return", "pantulan"],
        "response": """
            <strong>Apa itu Return Loss (ORL)?</strong><br><br>
            Return Loss atau <strong>Optical Return Loss (ORL)</strong> adalah 
            <strong>besaran sinyal yang dipantulkan kembali</strong> ke sumber.<br><br>
            <strong>Nilai Normal:</strong><br>
            • < -45 dB = Baik ✅<br>
            • -45 s/d -35 dB = Perlu Diwaspadai 🟡<br>
            • > -35 dB = Buruk 🔴<br><br>
            <strong>Mengapa Return Loss Penting?</strong><br>
            • Pantulan tinggi dapat merusak laser sumber<br>
            • Mengganggu kualitas sinyal<br>
            • Menandakan ada masalah di konektor atau sambungan<br><br>
            <strong>Di OptiM:</strong><br>
            Cek grafik <strong>"Return Loss per KM"</strong> di Dashboard.
        """
    },
    "prx": {
        "keywords": ["prx", "received power", "daya terima", "rx power"],
        "response": """
            <strong>Apa itu PRX (Received Power)?</strong><br><br>
            PRX atau <strong>Received Power</strong> adalah <strong>daya sinyal yang diterima</strong> 
            oleh perangkat penerima di ujung jaringan.<br><br>
            <strong>Nilai Normal:</strong><br>
            • -14 s/d -20 dBm = Sinyal Kuat ✅<br>
            • -20 s/d -24 dBm = Sinyal Cukup 🟡<br>
            • < -24 dBm = Sinyal Lemah 🔴<br><br>
            <strong>Penyebab PRX Rendah:</strong><br>
            • Loss tinggi di sepanjang jalur<br>
            • Konektor kotor<br>
            • Jarak terlalu jauh<br>
            • Fiber Cut<br><br>
            <strong>Di OptiM:</strong><br>
            Cek grafik <strong>"Signal Power (PRX) Monitoring"</strong> di Dashboard.
        """
    },
    "jenis_gangguan": {
        "keywords": ["gangguan", "jenis gangguan", "klasifikasi", "fiber cut", "bending", "dirty", "splice", "gap", "nearly"],
        "response": """
            <strong>Jenis-Jenis Gangguan Fiber Optik</strong><br><br>
            <strong>1. Bending (Tekukan Makro)</strong> 🔄<br>
            Terjadi saat kabel tertekuk melebihi radius minimum.<br>
            <strong>Solusi:</strong> Periksa jalur kabel, rapihkan di OTB.<br><br>
            
            <strong>2. Dirty Connector (Konektor Kotor)</strong> 🧹<br>
            Masalah paling umum! Debu di ujung konektor.<br>
            <strong>Solusi:</strong> Bersihkan dengan Fiber Cleaning Cassette.<br><br>
            
            <strong>3. Fiber Cut (Kabel Putus)</strong> ❌<br>
            Gangguan kritis! Sinyal hilang total.<br>
            <strong>Solusi:</strong> Cari titik putus dengan OTDR, sambung ulang.<br><br>
            
            <strong>4. Bad Splice (Sambungan Buruk)</strong> 🔗<br>
            Proses fusion splicing tidak sempurna.<br>
            <strong>Solusi:</strong> Potong dan splicing ulang.<br><br>
            
            <strong>5. Air Gap (Celah Udara)</strong> 💨<br>
            Ada ruang udara di antara konektor.<br>
            <strong>Solusi:</strong> Pastikan konektor terkunci rapat.<br><br>
            
            <strong>6. Nearly Cut (Hampir Putus)</strong> ⚠️<br>
            Kondisi kritis! Sinyal sangat lemah.<br>
            <strong>Solusi:</strong> Segera jadwalkan pemeliharaan preventif.
        """
    },
    "upload_foto": {
        "keywords": ["upload", "foto", "gambar", "ocr", "detection", "cara pakai", "tutorial"],
        "response": """
            <strong>Cara Menggunakan Fitur Upload Foto OTDR</strong><br><br>
            <strong>Langkah 1:</strong> Buka halaman <strong>Detection</strong><br>
            <strong>Langkah 2:</strong> Klik tombol <strong>"Upload Foto OTDR"</strong><br>
            <strong>Langkah 3:</strong> Pilih foto hasil printout OTDR<br>
            <strong>Langkah 4:</strong> Tunggu sistem memproses dengan OCR & ML<br>
            <strong>Langkah 5:</strong> Hasil klasifikasi akan muncul otomatis<br><br>
            <strong>Tips:</strong><br>
            • Pastikan foto <strong>jelas</strong> dan <strong>terang</strong><br>
            • Tabel OTDR harus <strong>terbaca</strong> (tidak terpotong)<br>
            • Hasil terbaik: foto dari <strong>printout OTDR</strong> langsung<br><br>
            <strong>Fitur ini berguna untuk:</strong><br>
            • Teknisi di lapangan yang ingin cepat cek hasil OTDR<br>
            • Analisis cepat tanpa input manual
        """
    },
    "input_manual": {
        "keywords": ["manual input", "input manual", "detection manual", "isi manual"],
        "response": """
            <strong>Cara Menggunakan Fitur Input Manual</strong><br><br>
            <strong>Langkah 1:</strong> Buka halaman <strong>Detection</strong><br>
            <strong>Langkah 2:</strong> Pilih tab <strong>"Manual Input"</strong><br>
            <strong>Langkah 3:</strong> Isi semua parameter OTDR:<br>
            • PRX (dBm)<br>
            • Loss 1-4 (dB)<br>
            • Return 1-4 (dB)<br>
            • Distance 1-4 (km)<br>
            <strong>Langkah 4:</strong> Klik <strong>"Proses Data"</strong><br><br>
            <strong>Kapan pakai fitur ini?</strong><br>
            • Saat foto OTDR tidak jelas<br>
            • Ingin menguji data tertentu<br>
            • Ingin mensimulasikan skenario gangguan
        """
    },
    "dashboard": {
        "keywords": ["dashboard", "monitoring", "data", "statistik"],
        "response": """
            <strong>Fitur Dashboard OptiM</strong><br><br>
            <strong>1. Total Measurement</strong><br>
            Menampilkan jumlah total data yang sudah diproses.<br><br>
            <strong>2. Status Normal vs Gangguan</strong><br>
            Memisahkan data normal dan yang terdeteksi gangguan.<br><br>
            <strong>3. Loss per KM</strong><br>
            Grafik redaman di setiap kilometer (1-4).<br>
            Garis merah = batas aman 1.2 dB.<br><br>
            <strong>4. Return Loss per KM</strong><br>
            Grafik pantulan sinyal di setiap kilometer.<br><br>
            <strong>5. Signal Power (PRX)</strong><br>
            Grafik daya terima. Garis merah = batas aman -24 dBm.<br><br>
            <strong>6. Fault Distribution</strong><br>
            Pie chart distribusi jenis gangguan.<br><br>
            <strong>7. Network Topology</strong><br>
            Visualisasi jaringan fiber optik.<br><br>
            <strong>8. Prediction Results Table</strong><br>
            Tabel detail semua hasil prediksi.
        """
    }
}

def format_fault_detail(faults_map: dict) -> str:
    """Format detail gangguan menjadi HTML"""
    if not faults_map:
        return "✅ Tidak ada gangguan terdeteksi"
    
    html = "<ul style='margin: 4px 0; padding-left: 20px;'>"
    for (klasifikasi, status), count in faults_map.items():
        emoji = "🔴" if status == "Critical" else "🟡" if status == "Warning" else "🟢"
        html += f"<li><strong>{klasifikasi}</strong> ({status}): {count} kali {emoji}</li>"
    html += "</ul>"
    return html

def get_rekap_response(query: str, context: dict) -> str | None:
    """Buat response rekap berdasarkan query dan context"""
    query_lower = query.lower()
    
    # Cek jenis rekap yang diminta
    rekap_type = None
    
    if any(x in query_lower for x in ["hari ini", "today", "sekarang", "hari"]):
        rekap_type = "hari_ini"
    elif any(x in query_lower for x in ["kemarin", "yesterday"]):
        rekap_type = "kemarin"
    elif any(x in query_lower for x in ["7 hari", "minggu", "seminggu", "week"]):
        rekap_type = "7_hari"
    elif any(x in query_lower for x in ["30 hari", "bulan", "sebulan", "month"]):
        rekap_type = "30_hari"
    elif any(x in query_lower for x in ["semua", "keseluruhan", "total", "all", "laporan"]):
        rekap_type = "semua"
    else:
        return None
    
    # Ambil data dari context
    total = context.get("Total Data", 0)
    normal = context.get("Data Normal", 0)
    gangguan = context.get("Data Gangguan", 0)
    fault_detail = context.get("Rincian Gangguan", "Tidak ada data")
    
    # Format detail
    detail = fault_detail if fault_detail else "✅ Tidak ada gangguan"
    
    # Status
    status = "🟢 Normal" if gangguan == 0 else "🟡 Ada Gangguan" if gangguan < 5 else "🔴 Banyak Gangguan"
    
    # Trend atau status tambahan
    trend = ""
    performance = ""
    
    if rekap_type in ["7_hari", "30_hari"]:
        if gangguan > 0:
            trend = "⚠️ Masih ada gangguan yang perlu ditangani"
            if gangguan > 5:
                trend = "🚨 Gangguan cukup tinggi, perlu evaluasi menyeluruh"
        else:
            trend = "✅ Kondisi jaringan stabil, tidak ada gangguan"
    
    if rekap_type == "semua":
        if gangguan == 0:
            performance = "✅ Sistem berjalan dengan sangat baik!"
        elif gangguan < total * 0.1:
            performance = "🟢 Performa baik, gangguan minimal (< 10%)"
        elif gangguan < total * 0.2:
            performance = "🟡 Perlu perhatian, gangguan sedang (10-20%)"
        else:
            performance = "🔴 Perlu evaluasi serius, gangguan tinggi (> 20%)"
    
    # Nama rekap
    rekap_names = {
        "hari_ini": "Hari Ini",
        "kemarin": "Kemarin",
        "7_hari": "7 Hari Terakhir",
        "30_hari": "30 Hari Terakhir",
        "semua": "Keseluruhan Sistem OptiM"
    }
    
    rekap_name = rekap_names.get(rekap_type, "Rekap")
    
    # Bangun response
    response = f"""
        <strong>📊 Rekap Gangguan {rekap_name}</strong><br><br>
        Total Pengukuran: <strong>{total}</strong><br>
        Gangguan Terdeteksi: <strong>{gangguan}</strong><br>
        Normal: <strong>{normal}</strong><br><br>
        <strong>📋 Rincian Gangguan:</strong><br>
        {detail}
    """
    
    if trend:
        response += f"<br><strong>📈 Trend:</strong> {trend}"
    
    if performance:
        response += f"<br><strong>📈 Kinerja:</strong> {performance}"
    
    if not trend and not performance:
        response += f"<br><br><strong>📈 Status:</strong> {status}"
    
    return response

def get_local_knowledge_response(query: str, context: dict = None) -> str | None:
    """Cari jawaban dari local knowledge base berdasarkan kata kunci"""
    query_lower = query.lower()
    
    # 🔥 CEK REKAP GANGGUAN DULU (prioritas tinggi)
    if context:
        rekap_response = get_rekap_response(query, context)
        if rekap_response:
            return rekap_response
    
    # Cek knowledge lainnya
    for key, data in LOCAL_KNOWLEDGE.items():
        for keyword in data["keywords"]:
            if keyword in query_lower:
                return data["response"]
    
    return None

# ═══════════════════════════════════════════════════════════════════
# CHAT ENDPOINT (DIPERBAIKI - DENGAN LOCAL KNOWLEDGE BASE)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/chat")
async def chat(
    request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user)
):
    user_message = request.get("message", "").strip()
    context_state = request.get("context_state", {})
    
    if not user_message:
        return {"response": "Pesan tidak boleh kosong.", "source": "error"}
    
    # 🔥 CEK LOCAL KNOWLEDGE BASE DULU
    local_response = get_local_knowledge_response(user_message, context_state)
    if local_response:
        logger.info(f"[CHAT] Menggunakan Local Knowledge untuk: {user_message[:50]}")
        return {"response": local_response, "source": "local_knowledge"}
    
    # 🔥 COBA GEMINI AI
    try:
        if gemini_client and GEMINI_API_KEY:
            try:
                # Coba pakai Gemini
                prompt = f"""
                Anda adalah asisten OptiM. Jawab pertanyaan ini dengan ringkas dan informatif.
                Pertanyaan: {user_message}
                Context: {json.dumps(context_state, indent=2)}
                """
                
                # Pakai client yang sesuai
                if gemini_model_name == "gemini-2.5-flash":
                    response = gemini_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt
                    )
                    reply = response.text
                else:
                    # Legacy client
                    reply = gemini_client.generate_content(prompt).text
                
                if reply:
                    logger.info(f"[CHAT] Gemini AI berhasil merespon")
                    return {"response": reply, "source": "gemini_ai"}
                    
            except Exception as e:
                logger.warning(f"[CHAT] Gemini AI error: {e}")
    
    except Exception as e:
        logger.warning(f"[CHAT] Gemini AI tidak tersedia: {e}")
    
    # 🔥 FALLBACK: DB Aware Response
    try:
        now_wib = datetime.utcnow() + timedelta(hours=7)
        
        # Ambil data sheets (source="sheets") + data user jika login
        if current_user:
            stmt_all = select(OtdrResult).where(
                (OtdrResult.source == "sheets") | 
                (OtdrResult.user_id == current_user.id)
            ).order_by(OtdrResult.timestamp.asc())
        else:
            stmt_all = select(OtdrResult).where(
                OtdrResult.source == "sheets"
            ).order_by(OtdrResult.timestamp.asc())
        
        result_all = await db.execute(stmt_all)
        all_records = result_all.scalars().all()
        
        logger.info(f"[CHAT] Total records fetched: {len(all_records)}")
        
        current_index = request.get("current_index", None)
        if current_index is not None:
            try:
                idx = int(current_index)
                if 0 <= idx < len(all_records):
                    all_records = all_records[:idx + 1]
            except Exception:
                pass
        
        today_date = now_wib.date()
        week_start_date = today_date - timedelta(days=6)
        month_start_date = today_date.replace(day=1)
        
        today_total = 0
        today_g = 0
        today_faults = {}
        
        week_total = 0
        week_g = 0
        week_faults = {}
        
        month_total = 0
        month_g = 0
        month_faults = {}
        
        total_data = len(all_records)
        all_g_total = 0
        all_faults = {}
        
        latest = all_records[-1] if all_records else None
        
        for rec in all_records:
            if not rec.timestamp:
                continue
            rec_wib = rec.timestamp + timedelta(hours=7)
            rec_date = rec_wib.date()
            
            is_fault = (rec.klasifikasi != "Normal")
            
            if is_fault:
                all_g_total += 1
                all_faults[(rec.klasifikasi, rec.status)] = all_faults.get((rec.klasifikasi, rec.status), 0) + 1
            
            if rec_date == today_date:
                today_total += 1
                if is_fault:
                    today_g += 1
                    today_faults[(rec.klasifikasi, rec.status)] = today_faults.get((rec.klasifikasi, rec.status), 0) + 1
            
            if rec_date >= week_start_date:
                week_total += 1
                if is_fault:
                    week_g += 1
                    week_faults[(rec.klasifikasi, rec.status)] = week_faults.get((rec.klasifikasi, rec.status), 0) + 1
            
            if rec_date >= month_start_date:
                month_total += 1
                if is_fault:
                    month_g += 1
                    month_faults[(rec.klasifikasi, rec.status)] = month_faults.get((rec.klasifikasi, rec.status), 0) + 1
        
        def fmt_detail(faults_map):
            if not faults_map:
                return "Tidak ada gangguan"
            return ", ".join([f"{k} ({s}): {c} kali" for (k, s), c in faults_map.items()])
        
        today_detail = fmt_detail(today_faults)
        week_detail = fmt_detail(week_faults)
        month_detail = fmt_detail(month_faults)
        all_detail = fmt_detail(all_faults)
        
        daily_records = []
        history_records = list(reversed(all_records))[:150]
        
    except Exception as db_err:
        logger.error(f"[CHAT] DB error: {db_err}")
        return {"response": get_local_chatbot_response(user_message), "source": "local_fallback"}
    
    reply = make_db_aware_response(
        user_message, today_total, today_g, today_detail,
        week_total, week_g, week_detail,
        month_total, month_g, month_detail,
        total_data, all_g_total, all_detail, latest,
        daily_records, history_records
    )
    
    # Kalau reply masih default, kasih response standar
    if reply == BOT_RESPONSES["default"]:
        reply = """
            <strong>Maaf, saya tidak bisa menjawab pertanyaan tersebut.</strong><br><br>
            Saya bisa membantu Anda dengan:<br>
            • Penjelasan <strong>Loss</strong>, <strong>Return Loss</strong>, dan <strong>PRX</strong><br>
            • Jenis-jenis <strong>gangguan</strong> fiber optik<br>
            • Cara menggunakan fitur <strong>Upload Foto</strong> dan <strong>Input Manual</strong><br>
            • Analisis <strong>data dashboard</strong> dan <strong>statistik</strong>
        """
    
    return {"response": reply, "source": "db_fallback"}

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

@app.post("/api/detect")
async def detect_ocr(
    file: UploadFile = File(...),
    prx_manual: float = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    allowed = {"image/jpeg", "image/png", "image/jpg", "image/bmp", "image/tiff"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Format gambar tidak didukung.")
    
    content = await file.read()
    raw_text = ""
    logger.info(f"📝 RAW TEXT FULL:\n{raw_text}")
    ocr_method = "none"
    
    logger.info("=" * 70)
    logger.info("🔄 Starting OCR process...")
    
    try:
        results = {}
        
        try:
            results['tesseract'] = await asyncio.wait_for(
                asyncio.to_thread(tesseract_extract, content), timeout=25.0)
        except asyncio.TimeoutError:
            logger.warning("Tesseract timed out")
            results['tesseract'] = ""

        try:
            results['ocr.space'] = await asyncio.wait_for(
                asyncio.to_thread(ocr_space_extract, content), timeout=15.0)
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
    
    if not raw_text or len(raw_text.strip()) < 20:
        raise HTTPException(
            status_code=400,
            detail="Gambar tidak dapat dibaca. Pastikan foto jelas dan tabel OTDR terlihat."
        )
    
    logger.info(f"📝 RAW TEXT ({ocr_method}):\n{raw_text[:500]}")
    
    rows, avg_total = parse_otdr_table_simple(raw_text)
    
    prx_from_ocr = extract_prx(raw_text)
    final_prx = prx_manual if prx_manual is not None else (prx_from_ocr if prx_from_ocr else -25.0)
    
    logger.info(f"📊 Parsed rows: {len(rows)}")
    for i, row in enumerate(rows):
        logger.info(f"   KM{i+1}: dist={row['distance']} loss={row['loss']} total_l={row['total_l']}")
    
    valid = [r for r in rows if r['distance'] > 0.5]
    if len(valid) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"Hanya {len(valid)} baris valid terdeteksi (butuh minimal 2)."
        )
    
    logger.info("=" * 50)
    logger.info("Starting ML Prediction...")
    
    otdr_values = {
        'Prx (dBm)': final_prx,
        'Distance 1': rows[0]['distance'], 'Distance 2': rows[1]['distance'],
        'Distance 3': rows[2]['distance'], 'Distance 4': rows[3]['distance'],
        'Loss 1': rows[0]['loss'], 'Loss 2': rows[1]['loss'], 'Loss 3': rows[2]['loss'],
        'Total-L 1': rows[0]['total_l'], 'Total-L 2': rows[1]['total_l'],
        'Total-L 3': rows[2]['total_l'], 'Total-L 4': rows[3]['total_l'],
        'Avg-L 1': rows[0]['avg_l'], 'Avg-L 2': rows[1]['avg_l'],
        'Avg-L 3': rows[2]['avg_l'], 'Avg-L 4': rows[3]['avg_l'],
        'Avg-Total': avg_total,
        'Return 1': rows[0]['return'], 'Return 2': rows[1]['return'],
        'Return 3': rows[2]['return'], 'Return 4': rows[3]['return'],
    }
    
    try:
        pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)
        logger.info(f"🤖 ML prediction SUCCESS: {pred.get('prediction')}")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED: {e}")
        pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
    
    logger.info("=" * 50)
    logger.info("💾 Saving to Database...")
    user_id = current_user.id if current_user else 1
    
    try:
        record = OtdrResult(
            user_id=user_id,
            timestamp=datetime.now(),
            prx=final_prx,
            loss_1=rows[0]['loss'], loss_2=rows[1]['loss'],
            loss_3=rows[2]['loss'], loss_4=None,
            return_1=rows[0]['return'], return_2=rows[1]['return'],
            return_3=rows[2]['return'], return_4=rows[3]['return'],
            distance_1=rows[0]['distance'], distance_2=rows[1]['distance'],
            distance_3=rows[2]['distance'], distance_4=rows[3]['distance'],
            total_l_1=rows[0]['total_l'], total_l_2=rows[1]['total_l'],
            total_l_3=rows[2]['total_l'], total_l_4=rows[3]['total_l'],
            avg_l_1=rows[0]['avg_l'], avg_l_2=rows[1]['avg_l'],
            avg_l_3=rows[2]['avg_l'], avg_l_4=rows[3]['avg_l'],
            avg_total=avg_total,
            klasifikasi=pred.get("prediction"),
            status=pred.get("status"),
            confidence=pred.get("confidence"),
            source="ocr",
            raw_text=raw_text[:1000],
        )
        
        db.add(record)
        await db.commit()
        await db.refresh(record)
        logger.info(f"✅ Saved to DB: ID={record.id}")
    
        status_str = pred.get("status", "Normal")
        if status_str.lower() in ["warning", "critical"]:
            logger.info(f"[TELEGRAM] Mengirim alert untuk: {pred.get('prediction')}")
            try:
                await asyncio.to_thread(
                    send_telegram_alert,
                    classification=pred.get("prediction"),
                    status=status_str,
                    loss=[rows[0]['loss'], rows[1]['loss'], rows[2]['loss'], rows[3]['loss']],
                    rl=[rows[0]['return'], rows[1]['return'], rows[2]['return'], rows[3]['return']],
                    prx=final_prx,
                    distances=[rows[0]['distance'], rows[1]['distance'], rows[2]['distance'], rows[3]['distance']],
                    timestamp=record.timestamp
                )
                record.telegram_alert_sent = True
                await db.commit()
            except Exception as tg_err:
                logger.error(f"[TELEGRAM] Error: {tg_err}")
        
    except Exception as e:
        logger.error(f"❌ DATABASE ERROR: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    logger.info("=" * 70)
    
    return {
        "message": "Gambar berhasil diproses",
        "raw_text": raw_text[:500],
        "extracted": {
            "distances": [rows[i]['distance'] for i in range(4)],
            "losses": [rows[i]['loss'] for i in range(4)],
            "total_ls": [rows[i]['total_l'] for i in range(4)],
            "avg_ls": [rows[i]['avg_l'] for i in range(4)],
            "returns": [rows[i]['return'] for i in range(4)],
        },
        "per_km": {"km1": rows[0], "km2": rows[1], "km3": rows[2], "km4": rows[3]},
        "prx": final_prx,
        "prediction": pred.get("prediction"),
        "confidence": pred.get("confidence"),
        "status": pred.get("status"),
        "id": record.id,
        "ocr_method": ocr_method,
    }

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
            "total_l_4": r.total_l_4,
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
        print(f"Kolom yang tersedia: {df.columns.tolist()}")
    
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
            # 🔥 FUNGSI g() DIPERBAIKI DENGAN FALLBACK
            def g(col, default=0.0):
                try:
                    # Coba ambil langsung
                    val = row.get(col, default)
                    if pd.notna(val) and val != '' and val != '-':
                        return float(val)
                    return default
                except Exception:
                    return default
            
            # 🔥 AMBIL AVG-TOTAL DENGAN FALLBACK NAMA KOLOM
            avg_total = 0.0
            avg_total_cols = ['Avg-Total', 'Avg Total', 'AvgTotal', 'Average Total']
            for col_name in avg_total_cols:
                if col_name in df.columns:
                    val = row.get(col_name, 0.0)
                    try:
                        if pd.notna(val) and val != '' and val != '-':
                            avg_total = float(val)
                            print(f"✅ ROW {idx}: Avg-Total dari '{col_name}' = {avg_total}")
                            break
                    except:
                        continue
            else:
                # 🔥 FALLBACK: Hitung dari Avg-L
                avg_l_values = []
                for i in range(1, 5):
                    val = g(f'Avg-L {i}', 0.0)
                    if val > 0:
                        avg_l_values.append(val)
                if avg_l_values:
                    avg_total = sum(avg_l_values) / len(avg_l_values)
                    print(f"📊 ROW {idx}: Avg-Total dihitung dari Avg-L = {avg_total}")
            
            otdr_values = {
                'Prx (dBm)': g('Prx (dBm)'),
                'Distance 1': g('Distance 1'), 'Distance 2': g('Distance 2'),
                'Distance 3': g('Distance 3'), 'Distance 4': g('Distance 4'),
                'Loss 1': g('Loss 1'), 'Loss 2': g('Loss 2'), 'Loss 3': g('Loss 3'),
                'Total-L 1': g('Total-L 1'), 'Total-L 2': g('Total-L 2'),
                'Total-L 3': g('Total-L 3'), 'Total-L 4': g('Total-L 4'),
                'Avg-L 1': g('Avg-L 1'), 'Avg-L 2': g('Avg-L 2'),
                'Avg-L 3': g('Avg-L 3'), 'Avg-L 4': g('Avg-L 4'),
                'Avg-Total': avg_total,  # 🔥 PAKAI avg_total YANG SUDAH DIPARSE
                'Return 1': g('Return 1'), 'Return 2': g('Return 2'),
                'Return 3': g('Return 3'), 'Return 4': g('Return 4'),
            }
            
            pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)

            print(f"🔍 ROW {idx}: Avg-Total FINAL = {avg_total}")
            
            record = OtdrResult(
                user_id=current_user.id,
                timestamp=datetime.now(),
                prx=g('Prx (dBm)'),
                temperature=g('Temperature (C)'),
                wavelength=g('Wavelength'),
                pulse_width=g('Pulse Width (ns)'),
                distance_1=g('Distance 1'), distance_2=g('Distance 2'),
                distance_3=g('Distance 3'), distance_4=g('Distance 4'),
                loss_1=g('Loss 1'), loss_2=g('Loss 2'), loss_3=g('Loss 3'), loss_4=None if g('Loss 4') == 0 else g('Loss 4'),
                total_l_1=g('Total-L 1'), total_l_2=g('Total-L 2'),
                total_l_3=g('Total-L 3'), total_l_4=g('Total-L 4'),
                avg_l_1=g('Avg-L 1'), avg_l_2=g('Avg-L 2'),
                avg_l_3=g('Avg-L 3'), avg_l_4=g('Avg-L 4'),
                avg_total=avg_total,  # 🔥 PAKAI avg_total YANG SUDAH DIPARSE
                return_1=g('Return 1'), return_2=g('Return 2'),
                return_3=g('Return 3'), return_4=g('Return 4'),
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
    def g(key, default=0.0):
        try:
            val = payload.get(key, default)
            if val is None or val == "":
                return default
            return float(val)
        except:
            return default
            
    d1 = g('distance_1', 1.00447)
    d2 = g('distance_2', 2.00639)
    d3 = g('distance_3', 3.01036)
    d4 = g('distance_4', 4.01432)
    
    l1 = g('loss_1', 0.0)
    l2 = g('loss_2', 0.0)
    l3 = g('loss_3', 0.0)
    l4 = g('loss_4', 0.0)
    
    r1 = g('return_1', 0.0)
    r2 = g('return_2', 0.0)
    r3 = g('return_3', 0.0)
    r4 = g('return_4', 0.0)
    
    prx = g('prx', -15.6)
    
    tl1 = g('total_l_1', l1)
    tl2 = g('total_l_2', l1 + l2)
    tl3 = g('total_l_3', l1 + l2 + l3)
    tl4 = g('total_l_4', l1 + l2 + l3 + l4)
    
    al1 = g('avg_l_1', tl1 / d1 if d1 > 0 else 0.0)
    al2 = g('avg_l_2', tl2 / d2 if d2 > 0 else 0.0)
    al3 = g('avg_l_3', tl3 / d3 if d3 > 0 else 0.0)
    al4 = g('avg_l_4', tl4 / d4 if d4 > 0 else 0.0)
    avg_total = g('avg_total', tl4 / d4 if d4 > 0 else 0.0)
    
    otdr_values = {
        'Prx (dBm)': prx,
        'Distance 1': d1, 'Distance 2': d2, 'Distance 3': d3, 'Distance 4': d4,
        'Loss 1': l1, 'Loss 2': l2, 'Loss 3': l3,
        'Total-L 1': tl1, 'Total-L 2': tl2, 'Total-L 3': tl3, 'Total-L 4': tl4,
        'Avg-L 1': al1, 'Avg-L 2': al2, 'Avg-L 3': al3, 'Avg-L 4': al4,
        'Avg-Total': avg_total,
        'Return 1': r1, 'Return 2': r2, 'Return 3': r3, 'Return 4': r4,
    }
    
    logger.info(f"Manual input received: {otdr_values}")
    
    try:
        pred = ml.predict_from_otdr(otdr_values)
        logger.info(f"🤖 ML prediction SUCCESS (manual): {pred.get('prediction')}")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED (manual): {e}")
        pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
        
    user_id = current_user.id if current_user else 1
    
    try:
        record = OtdrResult(
            user_id=user_id,
            timestamp=datetime.utcnow(),
            prx=prx,
            distance_1=d1, distance_2=d2, distance_3=d3, distance_4=d4,
            loss_1=l1, loss_2=l2, loss_3=l3, loss_4=l4,
            total_l_1=tl1, total_l_2=tl2, total_l_3=tl3, total_l_4=tl4,
            avg_l_1=al1, avg_l_2=al2, avg_l_3=al3, avg_l_4=al4,
            avg_total=avg_total,
            return_1=r1, return_2=r2, return_3=r3, return_4=r4,
            klasifikasi=pred.get("prediction"),
            status=pred.get("status"),
            confidence=pred.get("confidence"),
            source="manual",
            raw_text="Manual Input Data",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        
        status_str = pred.get("status", "Normal")
        if status_str.lower() in ["warning", "critical"]:
            try:
                await asyncio.to_thread(
                    send_telegram_alert,
                    classification=pred.get("prediction"),
                    status=status_str,
                    loss=[l1, l2, l3, l4],
                    rl=[r1, r2, r3, r4],
                    prx=prx,
                    distances=[d1, d2, d3, d4],
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
            "losses": [l1, l2, l3, l4],
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
    }