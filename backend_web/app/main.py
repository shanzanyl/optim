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

# ═══════════════════════════════════════════════════════════════════
# FIBER CUT — HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def safe_float(val, default=None):
    """
    Konversi nilai ke float dengan aman.
    Kembalikan default (None) jika nilai tidak bisa dikonversi,
    atau jika nilai adalah simbol kosong / EOF (---, -, kosong).
    TIDAK pernah mengubah None/kosong → 0. Itu tugas pemanggil.
    """
    if val is None:
        return default
    if isinstance(val, float) and (val != val):  # NaN check
        return default
    s = str(val).strip()
    if s in ('', '-', '--', '---', 'n/a', 'N/A', 'nan', 'None'):
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def detect_otdr_mode(rows: list) -> str:
    """
    Auto-detect mode berdasarkan jumlah event valid dan pola loss.

    Mode yang mungkin:
      "normal"         — 4 event lengkap, loss km1-3 semua valid
      "fiber_cut_km3"  — km3 loss None/missing, hanya 3 event valid (atau km3 terdeteksi cut)
      "fiber_cut_km2"  — km2 loss None/missing, hanya 2 event valid (atau km2 terdeteksi cut)

    Aturan prioritas (dari spesifik ke umum):
    1. Hitung jumlah event/baris yang memiliki distance valid (> 0.5 km)
    2. Periksa loss pada setiap km: None = missing = kandidat cut point
    3. Kombinasikan dua sinyal di atas untuk keputusan final
    """
    def has_valid_loss(row):
        v = row.get('loss')
        return v is not None and v != 0.0 and not (isinstance(v, float) and v != v)

    def has_valid_distance(row):
        d = row.get('distance', 0)
        return d is not None and d > 0.5

    valid_rows = [r for r in rows if has_valid_distance(r)]
    n_valid = len(valid_rows)

    # Ambil loss per km (index 0-3 = km1-km4)
    loss = [None, None, None, None]
    for i, r in enumerate(rows[:4]):
        loss[i] = r.get('loss')

    l1_valid = loss[0] is not None and loss[0] != 0.0
    l2_valid = loss[1] is not None and loss[1] != 0.0
    l3_valid = loss[2] is not None and loss[2] != 0.0

    # --- Deteksi berdasarkan jumlah event valid ---
    if n_valid <= 2:
        # Hanya 1-2 baris yang punya distance → fiber cut paling awal
        # Preferensi: fiber_cut_km2 jika hanya km1 yang ada loss
        if l1_valid and not l2_valid:
            logger.info(f"[MODE] fiber_cut_km2 (n_valid={n_valid}, l2 missing)")
            return "fiber_cut_km2"
        logger.info(f"[MODE] fiber_cut_km2 (n_valid={n_valid} ≤ 2, default)")
        return "fiber_cut_km2"

    if n_valid == 3:
        # Tiga baris → fiber cut km3
        if l1_valid and l2_valid and not l3_valid:
            logger.info(f"[MODE] fiber_cut_km3 (n_valid=3, l3 missing)")
            return "fiber_cut_km3"
        # Kadang parser mengisi loss=0 bukan None → cek juga
        if l1_valid and l2_valid and (loss[2] == 0.0 or loss[2] is None):
            logger.info(f"[MODE] fiber_cut_km3 (n_valid=3, l3=0/None)")
            return "fiber_cut_km3"
        logger.info(f"[MODE] fiber_cut_km3 (n_valid=3, default)")
        return "fiber_cut_km3"

    # n_valid == 4 → periksa loss pattern
    if not l2_valid:
        logger.info(f"[MODE] fiber_cut_km2 (n_valid=4 but l2 missing)")
        return "fiber_cut_km2"

    if not l3_valid:
        logger.info(f"[MODE] fiber_cut_km3 (n_valid=4 but l3 missing)")
        return "fiber_cut_km3"

    logger.info(f"[MODE] normal (n_valid={n_valid}, all losses valid)")
    return "normal"


def detect_mode_from_payload(payload: dict) -> str:
    """
    Versi detect_otdr_mode untuk payload manual (dict dengan key loss_1, loss_2, dst).
    Digunakan di endpoint detect-manual.
    """
    def is_present(key):
        v = payload.get(key)
        if v is None or v == '' or v == 'null':
            return False
        f = safe_float(v)
        return f is not None and f != 0.0

    l1 = is_present('loss_1')
    l2 = is_present('loss_2')
    l3 = is_present('loss_3')

    # Hitung berapa km yang punya distance
    dist_count = sum(
        1 for k in ['distance_1', 'distance_2', 'distance_3', 'distance_4']
        if safe_float(payload.get(k), 0.0) > 0.5
    )

    if dist_count <= 2 or (l1 and not l2):
        logger.info(f"[MODE-PAYLOAD] fiber_cut_km2 (dist_count={dist_count}, l1={l1}, l2={l2})")
        return "fiber_cut_km2"

    if dist_count == 3 or (l1 and l2 and not l3):
        logger.info(f"[MODE-PAYLOAD] fiber_cut_km3 (dist_count={dist_count}, l3={l3})")
        return "fiber_cut_km3"

    logger.info(f"[MODE-PAYLOAD] normal (dist_count={dist_count}, l1={l1}, l2={l2}, l3={l3})")
    return "normal"


def normalize_for_model(
    l1, l2, l3,
    tl1, tl2, tl3, tl4,
    al1, al2, al3, al4,
    r1, r2, r3, r4,
    d1, d2, d3, d4,
    avg_total, prx,
    mode: str
) -> dict:
    """
    Normalisasi nilai OTDR ke format yang konsisten dengan training model.

    Model dilatih dengan:
      - Normal      : loss_1-3 = nilai asli, semua field numerik
      - Fiber cut   : loss pada titik cut dan setelahnya = float('inf')
                      field lain setelah cut = 0.0 (aman untuk model)

    Catatan penting tentang inf:
      - JSON tidak support inf secara native → harus dikonversi
        ke angka besar (misal 9999.0) sebelum serialisasi jika perlu
      - Di sini kita kembalikan dict Python dengan inf asli
      - Caller (endpoint) harus sanitasi sebelum JSON response
    INF_VAL dipakai sebagai proxy inf untuk model (sesuai training).
    """
    INF_VAL = float('inf')

    def safe(v, default=0.0):
        f = safe_float(v)
        return f if f is not None else default

    d1_ = safe(d1, 1.0)
    d2_ = safe(d2, d1_ + 1.0)
    d3_ = safe(d3, d2_ + 1.0)
    d4_ = safe(d4, d3_ + 1.0)

    prx_ = safe(prx, -15.6)

    if mode == "normal":
        ml_l1 = safe(l1, 0.0)
        ml_l2 = safe(l2, 0.0)
        ml_l3 = safe(l3, 0.0)
        ml_tl1 = safe(tl1, 0.0); ml_tl2 = safe(tl2, 0.0)
        ml_tl3 = safe(tl3, 0.0); ml_tl4 = safe(tl4, 0.0)
        ml_al1 = safe(al1, 0.0); ml_al2 = safe(al2, 0.0)
        ml_al3 = safe(al3, 0.0); ml_al4 = safe(al4, 0.0)
        ml_r1 = safe(r1, -45.0); ml_r2 = safe(r2, -45.0)
        ml_r3 = safe(r3, -45.0); ml_r4 = safe(r4, -45.0)
        ml_avg = safe(avg_total, 0.0)

    elif mode == "fiber_cut_km2":
        # km1 valid, km2 ke atas = inf / 0
        ml_l1 = safe(l1, 0.0)
        ml_l2 = INF_VAL   # titik cut
        ml_l3 = INF_VAL   # setelah cut
        ml_tl1 = safe(tl1, 0.0); ml_tl2 = safe(tl2, 0.0)
        ml_tl3 = 0.0; ml_tl4 = 0.0
        ml_al1 = safe(al1, 0.0); ml_al2 = safe(al2, 0.0)
        ml_al3 = 0.0; ml_al4 = 0.0
        ml_r1 = safe(r1, -45.0); ml_r2 = safe(r2, -45.0)
        ml_r3 = -45.0; ml_r4 = -45.0
        ml_avg = safe(avg_total) or (safe(tl2, 0.0) / d2_ if d2_ > 0 else 0.0)

    elif mode == "fiber_cut_km3":
        # km1-2 valid, km3 ke atas = inf / 0
        ml_l1 = safe(l1, 0.0)
        ml_l2 = safe(l2, 0.0)
        ml_l3 = INF_VAL   # titik cut
        ml_tl1 = safe(tl1, 0.0); ml_tl2 = safe(tl2, 0.0)
        ml_tl3 = safe(tl3, 0.0); ml_tl4 = 0.0
        ml_al1 = safe(al1, 0.0); ml_al2 = safe(al2, 0.0)
        ml_al3 = safe(al3, 0.0); ml_al4 = 0.0
        ml_r1 = safe(r1, -45.0); ml_r2 = safe(r2, -45.0)
        ml_r3 = safe(r3, -45.0); ml_r4 = -45.0
        ml_avg = safe(avg_total) or (safe(tl3, 0.0) / d3_ if d3_ > 0 else 0.0)

    else:
        # Fallback ke normal
        return normalize_for_model(
            l1, l2, l3, tl1, tl2, tl3, tl4,
            al1, al2, al3, al4, r1, r2, r3, r4,
            d1, d2, d3, d4, avg_total, prx, "normal"
        )

    return {
        'Prx (dBm)': prx_,
        'Distance 1': d1_, 'Distance 2': d2_, 'Distance 3': d3_, 'Distance 4': d4_,
        'Loss 1': ml_l1, 'Loss 2': ml_l2, 'Loss 3': ml_l3,
        # Loss 4 tidak dipakai model
        'Total-L 1': ml_tl1, 'Total-L 2': ml_tl2,
        'Total-L 3': ml_tl3, 'Total-L 4': ml_tl4,
        'Avg-L 1': ml_al1, 'Avg-L 2': ml_al2,
        'Avg-L 3': ml_al3, 'Avg-L 4': ml_al4,
        'Avg-Total': ml_avg,
        'Return 1': ml_r1, 'Return 2': ml_r2,
        'Return 3': ml_r3, 'Return 4': ml_r4,
    }


def sanitize_for_json(otdr_values: dict) -> dict:
    """
    Ganti inf dengan nilai besar yang aman untuk JSON serialisasi.
    Model sudah menerima nilai ini karena di training juga pakai nilai tinggi
    sebagai proxy inf (tergantung scaler). Fallback-nya: 9999.0.
    """
    INF_PROXY = 9999.0
    result = {}
    for k, v in otdr_values.items():
        if isinstance(v, float) and (v == float('inf') or v == float('-inf') or v != v):
            result[k] = INF_PROXY
        else:
            result[k] = v
    return result


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
# OCR PREPROCESSING (DIPERBAIKI - OPTIMASI)
# ═══════════════════════════════════════════════════════════════════

from PIL import Image, ImageEnhance
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

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
    Cek apakah hasil parsing cukup valid untuk diteruskan ke model.
    - Normal: minimal 3 km valid
    - Fiber cut km2: minimal 2 km valid (km1-2 punya distance)
    - Fiber cut km3: minimal 3 km valid
    KM4 loss selalu None (EOF), tidak dihitung sebagai invalid.
    """
    if len(rows) < 2:
        return False

    valid_count = 0
    for i, row in enumerate(rows):
        loss_val  = row.get('loss')
        total_val = row.get('total_l', 0) or 0
        dist_val  = row.get('distance', 0) or 0

        if i == 3:  # KM4: cukup punya distance atau total_l
            if dist_val > 0.5 or total_val > 0:
                valid_count += 1
        else:
            # KM1-3: valid jika loss ada, atau total_l > 0, atau distance > 0
            loss_ok = (loss_val is not None and loss_val > 0)
            if loss_ok or total_val > 0 or dist_val > 0.5:
                valid_count += 1

    # Fiber cut km2 hanya punya 2 baris valid → izinkan
    return valid_count >= 2


# ═══════════════════════════════════════════════════════════════════
# HYBRID PARSER (MENGGABUNGKAN 3 STRATEGI) - DIPERBAIKI
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_hybrid(raw_text: str) -> Tuple[List[Dict], float]:
    """
    Hybrid parser: coba vertical → horizontal → manual.
    Setelah berhasil, panggil detect_otdr_mode untuk menentukan skenario.
    """
    logger.info("🔄 Starting hybrid parser...")

    def _count_valid(rows):
        return sum(
            1 for i, r in enumerate(rows)
            if (i == 3 and (r.get('total_l', 0) or 0) > 0)
            or (i != 3 and ((r.get('loss') is not None and (r.get('loss') or 0) > 0)
                            or (r.get('total_l', 0) or 0) > 0
                            or (r.get('distance', 0) or 0) > 0.5))
        )

    # Strategy 1
    try:
        logger.info("📊 Trying vertical parser...")
        rows, avg = parse_otdr_table_simple(raw_text)
        if len(rows) >= 4:
            rows[3]['loss'] = None
        if is_valid_parsed_rows(rows):
            logger.info(f"✅ Vertical parser success: {_count_valid(rows)}/4 rows valid")
            return rows, avg
        logger.info(f"⚠️ Vertical parser: {_count_valid(rows)}/4 valid")
    except Exception as e:
        import traceback
        logger.warning(f"Vertical parser failed: {e}\n{traceback.format_exc()}")

    # Strategy 2
    try:
        logger.info("📊 Trying horizontal parser...")
        rows, avg = parse_otdr_horizontal(raw_text)
        if len(rows) >= 4:
            rows[3]['loss'] = None
        if is_valid_parsed_rows(rows):
            logger.info(f"✅ Horizontal parser success: {_count_valid(rows)}/4 rows valid")
            return rows, avg
        logger.info(f"⚠️ Horizontal parser: {_count_valid(rows)}/4 valid")
    except Exception as e:
        import traceback
        logger.warning(f"Horizontal parser failed: {e}\n{traceback.format_exc()}")

    # Strategy 3
    try:
        logger.info("📊 Trying manual parser...")
        rows, avg = parse_otdr_manual(raw_text)
        if len(rows) >= 4:
            rows[3]['loss'] = None
        if is_valid_parsed_rows(rows):
            logger.info(f"✅ Manual parser success: {_count_valid(rows)}/4 rows valid")
            return rows, avg
        logger.info(f"⚠️ Manual parser: {_count_valid(rows)}/4 valid")
    except Exception as e:
        import traceback
        logger.warning(f"Manual parser failed: {e}\n{traceback.format_exc()}")

    logger.warning("❌ All parsers failed, returning empty rows")
    return [
        {'distance': 1.0, 'section': 0.0, 'loss': 0.0, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
        {'distance': 2.0, 'section': 0.0, 'loss': 0.0, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
        {'distance': 3.0, 'section': 0.0, 'loss': None, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
        {'distance': 4.0, 'section': 0.0, 'loss': None, 'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0},
    ], 0.0


# ═══════════════════════════════════════════════════════════════════
# OTDR PARSER (DIPERBAIKI - TANPA ROUND)
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_table_simple(raw_text: str) -> Tuple[List[Dict], float]:
    """
    Parse teks OCR OTDR menggunakan pendekatan line-based.
    Setiap baris adalah satu event/row tabel.

    Perbaikan vs versi lama:
    - Distance: HANYA angka dengan >= 3 digit desimal (misal 1.00447).
      Angka seperti 3.1 / 4.2 adalah nomor event, di-skip.
    - Simbol --- / - / kosong pada kolom loss → None (bukan 0)
    - Deteksi dan koreksi loss ↔ total_l tertukar
    - Deteksi dan koreksi avg_l ↔ return tertukar
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
    # 2. CLEAN LINES
    # =====================================================
    lines = []
    for line in text.splitlines():
        line = re.sub(r'\s+', ' ', line).strip()
        if line:
            lines.append(line)

    # =====================================================
    # 3. DETECT EVENT LINES
    #    Hanya baris yang mengandung distance VALID:
    #    format x.NNN (minimal 3 digit desimal) untuk x in [1,2,3,4]
    #    Ini menyaring nomor event seperti 3.1, 4.2
    # =====================================================
    event_lines = []
    for line in lines:
        # Pattern: angka 1-4 diikuti titik dan minimal 3 digit
        if re.search(r'\b[1-4]\.\d{3,}\b', line):
            event_lines.append(line)

    # =====================================================
    # 4. MERGE BROKEN ROWS
    # =====================================================
    merged = []
    i = 0
    while i < len(event_lines):
        row = event_lines[i]
        # Hitung angka valid (bukan nomor event)
        nums = re.findall(r'-?\d+\.\d+', row)
        while len(nums) < 5 and i + 1 < len(event_lines):
            i += 1
            row += " " + event_lines[i]
            nums = re.findall(r'-?\d+\.\d+', row)
        merged.append(row)
        i += 1

    # =====================================================
    # 5. PARSE EACH ROW
    #    Deteksi simbol --- sebagai None pada posisi loss
    # =====================================================
    rows = []
    for idx, row in enumerate(merged):

        # Tandai posisi '---' sebelum dinumerikkan
        # Ganti --- dengan sentinel 0 dulu, track posisinya
        missing_marker = '__MISSING__'
        row_marked = re.sub(r'---+', missing_marker, row)
        row_marked = re.sub(r'(?<!\d)-(?!\d)', missing_marker, row_marked)  # lone dash

        # Pecah token
        tokens = row_marked.split()

        # ── Cari indeks distance (x.NNN, minimal 3 desimal, x in [1-4]) ──
        distance_idx = None
        distance = None
        for j, tok in enumerate(tokens):
            m_dist = re.match(r'^([1-4])\.(\d{3,})$', tok)
            if m_dist:
                candidate = float(tok)
                # double-check: bukan integer-like (mis. 3.100 sebagai 3.1)
                if len(m_dist.group(2)) >= 3:
                    distance_idx = j
                    distance = candidate
                    break

        if distance_idx is None or distance is None:
            continue

        # Ambil token setelah distance
        remaining = tokens[distance_idx + 1:]

        # Parse token: numerik atau MISSING
        parsed = []
        for tok in remaining:
            if tok == missing_marker:
                parsed.append(None)  # missing / ---
            else:
                try:
                    v = float(tok)
                    if abs(v) < 200:  # skip nilai aneh
                        parsed.append(v)
                except ValueError:
                    continue

        if len(parsed) < 4:
            continue  # tidak cukup data

        # Urutan kolom setelah distance: section, loss, total_l, avg_l, return
        # Index:                          0       1     2        3      4
        section    = parsed[0] if len(parsed) > 0 and parsed[0] is not None else 0.0
        loss_raw   = parsed[1] if len(parsed) > 1 else None
        total_raw  = parsed[2] if len(parsed) > 2 and parsed[2] is not None else 0.0
        avg_raw    = parsed[3] if len(parsed) > 3 and parsed[3] is not None else 0.0
        ret_raw    = parsed[4] if len(parsed) > 4 and parsed[4] is not None else -45.0

        # ── Koreksi loss ↔ total_l tertukar ──
        # Heuristik: loss seharusnya < total_l untuk km yang sama.
        # Jika loss > 5 dan total_l < 5 → kemungkinan tertukar.
        loss_val  = loss_raw  # bisa None
        total_val = total_raw if total_raw is not None else 0.0

        if loss_val is not None:
            if loss_val > 5.0 and total_val < loss_val and total_val > 0:
                logger.info(f"[SWAP] loss({loss_val}) > total_l({total_val}), swapping")
                loss_val, total_val = total_val, loss_val

        # ── Koreksi avg_l ↔ return tertukar ──
        # avg_l harusnya kecil (0.2 - 2.0 dB/km), return harusnya negatif besar (< -30)
        # Jika avg_raw > 10 dan ret_raw adalah nilai kecil positif → kemungkinan tertukar.
        avg_val = avg_raw
        ret_val = ret_raw
        if avg_val is not None and ret_val is not None:
            if avg_val > 10.0 and 0 < ret_val < 10.0:
                logger.info(f"[SWAP] avg_l({avg_val}) looks like return, swapping with ret({ret_val})")
                avg_val, ret_val = ret_val, avg_val
            # Pastikan return negatif
            if ret_val > 0:
                ret_val = -abs(ret_val)

        rows.append({
            "distance": distance,
            "section": section if section is not None else 0.0,
            "loss": loss_val,       # bisa None (dari ---)
            "total_l": total_val,
            "avg_l": avg_val if avg_val is not None else 0.0,
            "return": ret_val if ret_val is not None else -45.0,
        })

    # =====================================================
    # 6. SORT BY DISTANCE
    # =====================================================
    rows = sorted(rows, key=lambda x: x["distance"])

    # =====================================================
    # 7. KM4 loss = None (End of Fiber), pad rows jika perlu
    # =====================================================
    if len(rows) >= 4:
        rows[3]["loss"] = None
    elif len(rows) == 3:
        last_d = rows[-1]["distance"]
        rows.append({
            "distance": last_d + 1.0, "section": 0.0,
            "loss": None, "total_l": 0.0, "avg_l": 0.0, "return": -45.0
        })
    elif len(rows) == 2:
        last_d = rows[-1]["distance"]
        rows.append({
            "distance": last_d + 1.0, "section": 0.0,
            "loss": None, "total_l": 0.0, "avg_l": 0.0, "return": -45.0
        })
        rows.append({
            "distance": last_d + 2.0, "section": 0.0,
            "loss": None, "total_l": 0.0, "avg_l": 0.0, "return": -45.0
        })

    while len(rows) < 4:
        last_d = rows[-1]["distance"] if rows else 0.0
        km = len(rows) + 1
        rows.append({
            "distance": last_d + 1.0, "section": 0.0,
            "loss": None, "total_l": 0.0, "avg_l": 0.0, "return": -45.0
        })

    # =====================================================
    # 8. LOG
    # =====================================================
    logger.info("===== FINAL PARSED ROWS =====")
    for i, r in enumerate(rows, start=1):
        logger.info(f"  KM{i}: dist={r['distance']}, loss={r['loss']}, "
                    f"total_l={r['total_l']}, avg_l={r['avg_l']}, return={r['return']}")
    logger.info(f"AVG TOTAL = {avg_total}")

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
    
    # 🔥 GUNAKAN HYBRID PARSER
    rows, avg_total = parse_otdr_hybrid(raw_text)
    
    # KM4 loss = None (End of Fiber)
    if len(rows) >= 4:
        rows[3]['loss'] = None
    
    prx_from_ocr = extract_prx(raw_text)
    final_prx = prx_manual if prx_manual is not None else (prx_from_ocr if prx_from_ocr else -25.0)
    
    # Auto-detect mode
    detected_mode = detect_otdr_mode(rows)
    
    logger.info(f"📊 Parsed rows: {len(rows)} | Mode: {detected_mode}")
    for i, row in enumerate(rows):
        logger.info(f"   KM{i+1}: dist={row['distance']} loss={row['loss']} total_l={row['total_l']}")
    
    # Validasi minimal 1 baris punya distance
    valid = [r for r in rows if r['distance'] > 0.5]
    if len(valid) < 1:
        while len(rows) < 4:
            km = len(rows) + 1
            rows.append({"distance": float(km), "section": 0.0,
                         "loss": None, "total_l": 0.0, "avg_l": 0.0, "return": -45.0})
        return {
            "success": False,
            "error": f"Hanya {len(valid)} baris valid terdeteksi.",
            "needs_manual": True,
            "message": "Data tidak lengkap. Silakan input manual.",
            "detected_mode": detected_mode,
            "extracted": {
                "distances": [rows[i]['distance'] for i in range(4)],
                "losses": [rows[i]['loss'] for i in range(4)],
                "total_ls": [rows[i]['total_l'] for i in range(4)],
                "avg_ls": [rows[i]['avg_l'] for i in range(4)],
                "returns": [rows[i]['return'] for i in range(4)],
                "avg_total": avg_total if avg_total else 0,
            },
            "prx": final_prx,
            "per_km": {
                "km1": rows[0], "km2": rows[1], "km3": rows[2], "km4": rows[3],
            }
        }
    
    # Pad ke 4 baris
    while len(rows) < 4:
        last_d = rows[-1]["distance"] if rows else 0.0
        rows.append({"distance": last_d + 1.0, "section": 0.0,
                     "loss": None, "total_l": 0.0, "avg_l": 0.0, "return": -45.0})
    
    logger.info("=" * 70)
    logger.info(f"✅ OCR parsing completed | mode={detected_mode}")
    
    return {
        "success": True,
        "message": "OCR berhasil diekstrak. Silakan periksa dan edit data sebelum klasifikasi.",
        "raw_text": raw_text[:500],
        "ocr_method": ocr_method,
        "detected_mode": detected_mode,
        "extracted": {
            "distances": [rows[i]['distance'] for i in range(4)],
            "losses": [rows[i]['loss'] for i in range(4)],
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
    """
    Endpoint deteksi manual.
    - Field kosong / null dikirim sebagai None, BUKAN 0
    - Backend auto-detect mode (normal / fiber_cut_km2 / fiber_cut_km3)
    - Normalisasi ke format model sebelum inferensi (loss cut = inf)
    - Nilai asli (dengan None) disimpan ke DB untuk display --- di frontend
    """

    # ── Ambil nilai dari payload, None jika kosong ──
    def gn(key):
        """Ambil nilai sebagai float atau None jika tidak ada / kosong"""
        return safe_float(payload.get(key))

    def gf(key, default=0.0):
        """Ambil nilai sebagai float dengan fallback"""
        v = safe_float(payload.get(key))
        return v if v is not None else default

    prx = gf('prx', -15.6)

    d1 = gn('distance_1'); d2 = gn('distance_2')
    d3 = gn('distance_3'); d4 = gn('distance_4')

    l1 = gn('loss_1');  l2 = gn('loss_2');  l3 = gn('loss_3')
    # loss_4 selalu None (End of Fiber)

    tl1 = gn('total_l_1'); tl2 = gn('total_l_2')
    tl3 = gn('total_l_3'); tl4 = gn('total_l_4')

    al1 = gn('avg_l_1'); al2 = gn('avg_l_2')
    al3 = gn('avg_l_3'); al4 = gn('avg_l_4')

    r1 = gn('return_1'); r2 = gn('return_2')
    r3 = gn('return_3'); r4 = gn('return_4')

    avg_total = gn('avg_total')

    # ── Auto-detect mode ──
    mode = detect_mode_from_payload(payload)
    logger.info(f"[MANUAL] detected_mode={mode}")

    # ── Normalisasi ke format model ──
    otdr_values = normalize_for_model(
        l1, l2, l3,
        tl1, tl2, tl3, tl4,
        al1, al2, al3, al4,
        r1, r2, r3, r4,
        d1, d2, d3, d4,
        avg_total, prx,
        mode
    )

    # Sanitasi inf → angka besar sebelum masuk model (model menerima numpy float)
    otdr_values_clean = sanitize_for_json(otdr_values)
    logger.info(f"[MANUAL] otdr_values → model: {otdr_values_clean}")

    try:
        pred = ml.predict_from_otdr(otdr_values_clean)
        logger.info(f"🤖 ML prediction (manual, {mode}): {pred.get('prediction')}")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED (manual): {e}")
        pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}

    user_id = current_user.id if current_user else 1

    # Nilai untuk DB: pakai nilai asli (None kalau memang kosong / fiber cut)
    def db_val(v, fallback=None):
        return v if v is not None else fallback

    try:
        record = OtdrResult(
            user_id=user_id,
            timestamp=datetime.utcnow(),
            prx=prx,
            distance_1=db_val(d1), distance_2=db_val(d2),
            distance_3=db_val(d3), distance_4=db_val(d4),
            loss_1=db_val(l1), loss_2=db_val(l2),
            loss_3=db_val(l3), loss_4=None,   # selalu None (EOF)
            total_l_1=db_val(tl1, 0.0), total_l_2=db_val(tl2, 0.0),
            total_l_3=db_val(tl3, 0.0), total_l_4=db_val(tl4, 0.0),
            avg_l_1=db_val(al1, 0.0), avg_l_2=db_val(al2, 0.0),
            avg_l_3=db_val(al3, 0.0), avg_l_4=db_val(al4, 0.0),
            avg_total=db_val(avg_total, 0.0),
            return_1=db_val(r1, -45.0), return_2=db_val(r2, -45.0),
            return_3=db_val(r3, -45.0), return_4=db_val(r4, -45.0),
            klasifikasi=pred.get("prediction"),
            status=pred.get("status"),
            confidence=pred.get("confidence"),
            source="manual",
            raw_text=f"Manual Input | mode={mode}",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        status_str = pred.get("status", "Normal")
        if status_str.lower() in ["warning", "critical"]:
            try:
                loss_alert = [
                    l1 if l1 is not None else 0.0,
                    l2 if l2 is not None else 0.0,
                    l3 if l3 is not None else 0.0,
                    0.0
                ]
                await asyncio.to_thread(
                    send_telegram_alert,
                    classification=pred.get("prediction"),
                    status=status_str,
                    loss=loss_alert,
                    rl=[
                        r1 if r1 is not None else -45.0,
                        r2 if r2 is not None else -45.0,
                        r3 if r3 is not None else -45.0,
                        r4 if r4 is not None else -45.0,
                    ],
                    prx=prx,
                    distances=[
                        d1 if d1 is not None else 1.0,
                        d2 if d2 is not None else 2.0,
                        d3 if d3 is not None else 3.0,
                        d4 if d4 is not None else 4.0,
                    ],
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

    # Response: kembalikan nilai asli (None → frontend tampilkan ---)
    return {
        "message": "Data manual berhasil diproses",
        "detected_mode": mode,
        "extracted": {
            "distances": [d1, d2, d3, d4],
            "losses": [l1, l2, l3, None],     # loss_4 selalu None
            "total_ls": [tl1, tl2, tl3, tl4],
            "avg_ls": [al1, al2, al3, al4],
            "returns": [r1, r2, r3, r4],
            "avg_total": avg_total,
        },
        "prx": prx,
        "prx_source": "manual",
        "prediction": pred.get("prediction"),
        "confidence": pred.get("confidence"),
        "status": pred.get("status"),
        "id": record.id,
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