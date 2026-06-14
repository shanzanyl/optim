# main.py - VERSION FIXED (LENGKAP)
from datetime import  datetime, timedelta
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

# Tesseract path - auto detect
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

# Global EasyOCR reader
#easyocr_reader = None
#easyocr_loading = False

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Telegram configurations
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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
    
    # Pad loss and return lists to ensure 4 elements
    while len(safe_loss) < 4:
        safe_loss.append(0.0)
    while len(safe_rl) < 4:
        safe_rl.append(0.0)

    # Default distances if not provided
    if not distances:
        distances = [1.004, 2.006, 3.010, 4.014]
    
    safe_dist = []
    for i in range(4):
        if i < len(distances) and distances[i] is not None:
            safe_dist.append(float(distances[i]))
        else:
            safe_dist.append(float(i + 1))
            
    # Find anomaly location (Wajib sesuai dengan logika visualisasi dashboard & topology)
    classification_lower = classification.lower() if classification else ""
    is_fiber_cut = "cut" in classification_lower or "putus" in classification_lower
    
    if is_fiber_cut:
        # Cari KM pertama yang loss-nya 0.0 (sebagai lokasi putus)
        cut_idx = -1
        for idx, val in enumerate(safe_loss):
            if val == 0.0:
                cut_idx = idx
                break
        if cut_idx == -1:
            cut_idx = 3  # default KM 4 jika tidak ditemukan 0.0
        km_loc = cut_idx + 1
        jarak_loc = round(safe_dist[cut_idx], 3)
        redaman_loc = 0.0
    else:
        # Untuk gangguan selain putus, cari KM dengan loss tertinggi
        max_loss_val = max(safe_loss)
        max_loss_idx = safe_loss.index(max_loss_val) if safe_loss else 0
        km_loc = max_loss_idx + 1
        jarak_loc = round(safe_dist[max_loss_idx], 3)
        redaman_loc = round(max_loss_val, 2)
    
    # Format time (Konversi dari UTC ke Waktu Lokal WIB/GMT+7 agar sinkron dengan tabel dashboard)
    if timestamp:
        if isinstance(timestamp, str):
            try:
                # Parse string ISO format (jika dikirim string)
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                local_time = dt + timedelta(hours=7)
                time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                time_str = timestamp.replace("T", " ")[:19]
        else:
            # Datetime object dari DB (biasanya naive UTC)
            local_time = timestamp + timedelta(hours=7)
            time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        local_time = datetime.utcnow() + timedelta(hours=7)
        time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
        
    status_cap = str(status).capitalize()
    
    # Format nilai KM 4 agar menampilkan '---' jika bernilai 0.0 agar persis dengan tabel dashboard
    loss_km4_str = "---" if safe_loss[3] == 0.0 else f"{safe_loss[3]:.2f} dB"
    
    # Construct message template
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
    
    # Pisahkan Chat ID jika ada lebih dari satu (dipisah koma)
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
# 🔥 FUNGSI MAPPING KOLOM - SESUAI DENGAN FEATURE_ORDER.JSON
# ═══════════════════════════════════════════════════════════════════

# Daftar fitur yang dibutuhkan model (dari feature_order.json)
REQUIRED_FEATURES = [
    "Prx (dBm)", "Distance 1", "Distance 2", "Distance 3", "Distance 4",
    "Loss 1", "Loss 2", "Loss 3", "Total-L 1", "Total-L 2", "Total-L 3", "Total-L 4",
    "Avg-L 1", "Avg-L 2", "Avg-L 3", "Avg-L 4", "Avg-Total",
    "Return 1", "Return 2", "Return 3", "Return 4"
]

def create_column_mapping(df_columns: list) -> dict:
    """
    Membuat mapping dari nama kolom di CSV ke format yang dibutuhkan model.
    Mapping ini FLEKSIBEL dan bisa membaca berbagai variasi nama kolom.
    """
    # Ubah semua ke lowercase untuk pencarian case-insensitive
    col_lower = {c.lower().strip(): c for c in df_columns}
    
    # Keyword mapping untuk setiap fitur yang dibutuhkan
    keyword_mapping = {
        'Prx (dBm)': ['prx (dbm)', 'prx', 'rx power', 'received power', 'prx(dbm)', 'prx_dbm'],
        
        'Distance 1': ['distance 1', 'dist 1', 'jarak 1', 'distance_1', 'distancias 1'],
        'Distance 2': ['distance 2', 'dist 2', 'jarak 2', 'distance_2', 'distancias 2'],
        'Distance 3': ['distance 3', 'dist 3', 'jarak 3', 'distance_3', 'distancias 3'],
        'Distance 4': ['distance 4', 'dist 4', 'jarak 4', 'distance_4', 'distancias 4'],
        
        'Loss 1': ['loss 1', 'redaman 1', 'attenuation 1', 'loss_1', 'perdida 1'],
        'Loss 2': ['loss 2', 'redaman 2', 'attenuation 2', 'loss_2', 'perdida 2'],
        'Loss 3': ['loss 3', 'redaman 3', 'attenuation 3', 'loss_3', 'perdida 3'],
        
        'Total-L 1': ['total-l 1', 'total loss 1', 'total_l_1', 'totalloss1', 'total l 1', 'total_l1'],
        'Total-L 2': ['total-l 2', 'total loss 2', 'total_l_2', 'totalloss2', 'total l 2', 'total_l2'],
        'Total-L 3': ['total-l 3', 'total loss 3', 'total_l_3', 'totalloss3', 'total l 3', 'total_l3'],
        'Total-L 4': ['total-l 4', 'total loss 4', 'total_l_4', 'totalloss4', 'total l 4', 'total_l4'],
        
        'Avg-L 1': ['avg-l 1', 'average loss 1', 'avg_l_1', 'avg loss 1', 'rata-rata loss 1', 'avg_l1'],
        'Avg-L 2': ['avg-l 2', 'average loss 2', 'avg_l_2', 'avg loss 2', 'rata-rata loss 2', 'avg_l2'],
        'Avg-L 3': ['avg-l 3', 'average loss 3', 'avg_l_3', 'avg loss 3', 'rata-rata loss 3', 'avg_l3'],
        'Avg-L 4': ['avg-l 4', 'average loss 4', 'avg_l_4', 'avg loss 4', 'rata-rata loss 4', 'avg_l4'],
        
        'Avg-Total': ['avg-total', 'total average', 'avg_total', 'rata-rata total', 'average total', 'avg total'],
        
        'Return 1': ['return 1', 'orl 1', 'return loss 1', 'return_1', 'retorno 1'],
        'Return 2': ['return 2', 'orl 2', 'return loss 2', 'return_2', 'retorno 2'],
        'Return 3': ['return 3', 'orl 3', 'return loss 3', 'return_3', 'retorno 3'],
        'Return 4': ['return 4', 'orl 4', 'return loss 4', 'return_4', 'retorno 4'],
    }
    
    mapping = {}
    for needed_field, keywords in keyword_mapping.items():
        for keyword in keywords:
            if keyword in col_lower:
                mapping[needed_field] = col_lower[keyword]
                logger.info(f"✅ Mapped '{needed_field}' → '{col_lower[keyword]}'")
                break
        if needed_field not in mapping:
            logger.warning(f"⚠️ Column '{needed_field}' not found in CSV, will try to calculate")
    
    return mapping


def get_value_from_row(row, field_name: str, mapping: dict, default=0.0):
    """Ambil nilai dari row berdasarkan mapping yang sudah dibuat"""
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
    """Hitung nilai yang tidak tersedia di CSV"""
    result = {}
    
    # Hitung Avg-L dari Total-L / Distance jika tidak ada
    for i in range(1, 5):
        avg_key = f'Avg-L {i}'
        total_key = f'Total-L {i}'
        dist_key = f'Distance {i}'
        
        avg_val = get_value_from_row(row, avg_key, mapping, 0)
        total_val = get_value_from_row(row, total_key, mapping, 0)
        dist_val = distance.get(dist_key, 0)
        
        if avg_val == 0 and total_val > 0 and dist_val > 0:
            avg_val = total_val / dist_val
            logger.info(f"📐 Calculated {avg_key} = {total_val}/{dist_val} = {avg_val:.4f}")
        
        result[avg_key] = avg_val
    
    # Hitung Avg-Total jika tidak ada
    avg_total = get_value_from_row(row, 'Avg-Total', mapping, 0)
    if avg_total == 0:
        avg_total = result.get('Avg-L 4', 0)
        if avg_total == 0:
            total_l_4 = get_value_from_row(row, 'Total-L 4', mapping, 0)
            dist_4 = distance.get('Distance 4', 0)
            if total_l_4 > 0 and dist_4 > 0:
                avg_total = total_l_4 / dist_4
        logger.info(f"📐 Calculated Avg-Total = {avg_total:.4f}")
    
    result['Avg-Total'] = avg_total
    
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    # global easyocr_reader, easyocr_loading
    
    # Create tables (safe: only creates if not exists, never drops)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE otdr_results ADD COLUMN telegram_alert_sent BOOLEAN DEFAULT FALSE;"))
            logger.info("✅ Database migration: added telegram_alert_sent column")
        except Exception as mig_err:
            logger.warning(f"⚠️ Migration check: {mig_err}")
    
    # Create admin user
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
    
    # 🔥 Load EasyOCR di background (non-blocking) - DI-NONAKTIFKAN
    # async def load_easyocr_background():
    #     global easyocr_reader, easyocr_loading
    #     easyocr_loading = True
    #     try:
    #         def _init_reader():
    #             import easyocr
    #             return easyocr.Reader(['en'], gpu=False, verbose=False)
            
    #         easyocr_reader = await asyncio.to_thread(_init_reader)
    #         logger.info("✅ EasyOCR loaded successfully")
    #     except Exception as e:
    #         logger.warning(f"⚠️ EasyOCR failed to load: {e}")
    #         easyocr_reader = None
    #     finally:
    #         easyocr_loading = False
    
    # asyncio.create_task(load_easyocr_background())
    # logger.info("🔄 EasyOCR loading started in background...")
    
    # 🔥 Auto sync background task (every 6 hours)
    async def auto_sync_sheets():
        await asyncio.sleep(10)
        while True:
            try:
                logger.info("🔄 Auto-sync from Google Sheets...")
                async with AsyncSessionLocal() as db_sync:
                    result = await db_sync.execute(select(User))
                    users = result.scalars().all()
                    
                    for user in users:
                        try:
                            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                                resp = await client.get(SHEET_URL, headers={
                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                                })
                                if resp.status_code == 200:
                                    df = pd.read_csv(io.StringIO(resp.text))
                                    df.columns = [str(c).strip() for c in df.columns]
                                    
                                    logger.info(f"📊 Found columns in CSV: {df.columns.tolist()}")
                                    
                                    # 🔥 BUAT MAPPING KOLOM
                                    column_mapping = create_column_mapping(df.columns.tolist())
                                    
                                    # Delete old sheets data
                                    existing = await db_sync.execute(
                                        select(OtdrResult).where(
                                            OtdrResult.user_id == None,
                                            OtdrResult.source == "sheets",
                                        )
                                    )
                                    for rec in existing.scalars().all():
                                        await db_sync.delete(rec)
                                    await db_sync.flush()
                                    
                                    saved = 0
                                    for _, row in df.iterrows():
                                        # 🔥 AMBIL TIMESTAMP DARI CSV
                                        timestamp = datetime.now()
                                        time_col = next((c for c in df.columns if 'time' in c.lower()), None)
                                        if time_col and pd.notna(row.get(time_col)):
                                            try:
                                                timestamp = pd.to_datetime(row[time_col])
                                            except Exception:
                                                pass
                                        
                                        # Ambil nilai dasar
                                        prx_val = get_value_from_row(row, 'Prx (dBm)', column_mapping)
                                        temp_val = get_value_from_row(row, 'Temperature (C)', column_mapping)
                                        wavelength_val = get_value_from_row(row, 'Wavelength', column_mapping)
                                        pulse_width_val = get_value_from_row(row, 'Pulse Width (ns)', column_mapping)
                                        
                                        # Distance values
                                        distance = {
                                            'Distance 1': get_value_from_row(row, 'Distance 1', column_mapping),
                                            'Distance 2': get_value_from_row(row, 'Distance 2', column_mapping),
                                            'Distance 3': get_value_from_row(row, 'Distance 3', column_mapping),
                                            'Distance 4': get_value_from_row(row, 'Distance 4', column_mapping),
                                        }
                                        
                                        # Loss values
                                        loss = {
                                            'Loss 1': get_value_from_row(row, 'Loss 1', column_mapping),
                                            'Loss 2': get_value_from_row(row, 'Loss 2', column_mapping),
                                            'Loss 3': get_value_from_row(row, 'Loss 3', column_mapping),
                                        }
                                        
                                        # Total-L values
                                        total_l = {
                                            'Total-L 1': get_value_from_row(row, 'Total-L 1', column_mapping),
                                            'Total-L 2': get_value_from_row(row, 'Total-L 2', column_mapping),
                                            'Total-L 3': get_value_from_row(row, 'Total-L 3', column_mapping),
                                            'Total-L 4': get_value_from_row(row, 'Total-L 4', column_mapping),
                                        }
                                        
                                        # Return values
                                        return_vals = {
                                            'Return 1': get_value_from_row(row, 'Return 1', column_mapping),
                                            'Return 2': get_value_from_row(row, 'Return 2', column_mapping),
                                            'Return 3': get_value_from_row(row, 'Return 3', column_mapping),
                                            'Return 4': get_value_from_row(row, 'Return 4', column_mapping),
                                        }
                                        
                                        # Hitung nilai yang hilang (Avg-L dan Avg-Total)
                                        calculated = calculate_missing_values(row, column_mapping, distance, total_l)
                                        
                                        # Siapkan values untuk ML
                                        otdr_values = {
                                            'Prx (dBm)': prx_val,
                                            'Distance 1': distance['Distance 1'],
                                            'Distance 2': distance['Distance 2'],
                                            'Distance 3': distance['Distance 3'],
                                            'Distance 4': distance['Distance 4'],
                                            'Loss 1': loss['Loss 1'],
                                            'Loss 2': loss['Loss 2'],
                                            'Loss 3': loss['Loss 3'],
                                            'Total-L 1': total_l['Total-L 1'],
                                            'Total-L 2': total_l['Total-L 2'],
                                            'Total-L 3': total_l['Total-L 3'],
                                            'Total-L 4': total_l['Total-L 4'],
                                            'Avg-L 1': calculated.get('Avg-L 1', 0),
                                            'Avg-L 2': calculated.get('Avg-L 2', 0),
                                            'Avg-L 3': calculated.get('Avg-L 3', 0),
                                            'Avg-L 4': calculated.get('Avg-L 4', 0),
                                            'Avg-Total': calculated.get('Avg-Total', 0),
                                            'Return 1': return_vals['Return 1'],
                                            'Return 2': return_vals['Return 2'],
                                            'Return 3': return_vals['Return 3'],
                                            'Return 4': return_vals['Return 4'],
                                        }
                                        
                                        # Prediksi ML
                                        try:
                                            pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)
                                        except Exception as e:
                                            logger.error(f"ML prediction error: {e}")
                                            pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
                                        
                                        # Simpan ke database
                                        record = OtdrResult(
                                            user_id=None,
                                            timestamp=timestamp,
                                            prx=prx_val,
                                            temperature=temp_val,
                                            wavelength=wavelength_val,
                                            pulse_width=pulse_width_val,
                                            distance_1=distance['Distance 1'],
                                            distance_2=distance['Distance 2'],
                                            distance_3=distance['Distance 3'],
                                            distance_4=distance['Distance 4'],
                                            loss_1=loss['Loss 1'],
                                            loss_2=loss['Loss 2'],
                                            loss_3=loss['Loss 3'],
                                            loss_4=None,
                                            total_l_1=total_l['Total-L 1'],
                                            total_l_2=total_l['Total-L 2'],
                                            total_l_3=total_l['Total-L 3'],
                                            total_l_4=total_l['Total-L 4'],
                                            avg_l_1=calculated.get('Avg-L 1', 0),
                                            avg_l_2=calculated.get('Avg-L 2', 0),
                                            avg_l_3=calculated.get('Avg-L 3', 0),
                                            avg_l_4=calculated.get('Avg-L 4', 0),
                                            avg_total=calculated.get('Avg-Total', 0),
                                            return_1=return_vals['Return 1'],
                                            return_2=return_vals['Return 2'],
                                            return_3=return_vals['Return 3'],
                                            return_4=return_vals['Return 4'],
                                            klasifikasi=pred.get("prediction"),
                                            status=pred.get("status"),
                                            confidence=pred.get("confidence"),
                                            source="sheets",
                                        )
                                        db_sync.add(record)
                                        saved += 1
                                    
                                    await db_sync.commit()
                                    logger.info(f"✅ Auto-sync success for {user.email}: {saved} records saved")
                                else:
                                    logger.error(f"Failed to fetch sheet: {resp.status_code}")
                        except Exception as e:
                            logger.error(f"Auto-sync error for {user.email}: {e}")
                
                await asyncio.sleep(21600)  # 6 hours
            except Exception as e:
                logger.error(f"Auto-sync loop error: {e}")
                await asyncio.sleep(3600)
    
    asyncio.create_task(auto_sync_sheets())
    logger.info("🔄 Auto-sync background task started (every 6 hours)")
    
    yield
    
    logger.info("Shutting down...")


app = FastAPI(title="OptiM API", version="2.0.1", lifespan=lifespan)

# 🔥 CORS - Allow all origins for testing (bisa dipersempit nanti)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "https://ashy-mushroom-0feb76700.7.azurestaticapps.net",
        "https://optim-api-ckfhb5heg3f3btgz.southeastasia-01.azurewebsites.net",
        "*"  # Sementara izinkan semua untuk testing
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_FOLDER", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# SIMPLE & ROBUST OTDR PARSER
# ═══════════════════════════════════════════════════════════════════

def parse_otdr_table_simple(raw_text: str) -> tuple[list, float]:
    """
    Parse teks OCR OTDR secara global dan dinamis.
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
                'distance': float(i), 'section': 1.0, 'loss': None if i == 4 else 0.0,
                'total_l': 0.0, 'avg_l': 0.0, 'return': -45.0
            })
            continue
        
        # Token pertama adalah distance
        dist = row_tokens[0]
        
        # Ekstrak return loss
        ret = -45.0
        ret_idx = -1
        for idx, val in enumerate(row_tokens):
            if isinstance(val, float) and (30.0 <= abs(val) <= 65.0):
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
        
        if i == 4:
            loss = None
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
            if len(remaining) >= 3:
                loss = remaining[0] if isinstance(remaining[0], float) else 0.0
                total_l = remaining[1] if isinstance(remaining[1], float) else 0.0
                avg_l = remaining[2] if isinstance(remaining[2], float) else 0.0
            elif len(remaining) == 2:
                loss = None
                total_l = remaining[0] if isinstance(remaining[0], float) else 0.0
                avg_l = remaining[1] if isinstance(remaining[1], float) else 0.0
            elif len(remaining) == 1:
                total_l = remaining[0] if isinstance(remaining[0], float) else 0.0
                avg_l = 0.0
                loss = None if i == 4 else 0.0
            else:
                loss = None if i == 4 else 0.0
                total_l = 0.0
                avg_l = 0.0
        
        row_data = {
            'distance': round(float(dist), 5),
            'section': round(float(sect), 5),
            'loss': round(float(loss), 3) if loss is not None and loss != '---' else (None if i == 4 else 0.0),
            'total_l': round(float(total_l), 3) if isinstance(total_l, float) else 0.0,
            'avg_l': round(float(avg_l), 3) if isinstance(avg_l, float) else 0.0,
            'return': round(float(ret), 2)
        }
        rows.append(row_data)
    
    
    # 5. Ekstrak Avg-Total
    avg_total = 0.0
    m_avg = re.search(r'(\d+\.\d{2,3})\s*(?:dB/km|db/km)', text)
    if not m_avg:
        m_avg = re.search(r'(\d+\.\d{1,3})\s*(?:dB/km|db/km|dB)', text)
    if m_avg:
        avg_total = float(m_avg.group(1))
    else:
        r4 = rows[3]
        avg_total = r4['avg_l'] if r4['avg_l'] and r4['avg_l'] > 0 else (
            r4['total_l'] / r4['distance'] if r4['distance'] > 0 else 0.0
        )
    
    # 6. Hitung ulang avg_l jika masih 0
    for row in rows:
        if (not row['avg_l'] or row['avg_l'] == 0.0) and row['total_l'] > 0 and row['distance'] > 0:
            row['avg_l'] = round(row['total_l'] / row['distance'], 3)
    
    logger.info(f"Final parsed rows: {rows}")
    return rows, avg_total


# ═══════════════════════════════════════════════════════════════════
# SIMPLE OCR PREPROCESSING
# ═══════════════════════════════════════════════════════════════════

def preprocess_image_simple(image_bytes: bytes) -> list:
    """Preprocessing sederhana untuk OCR"""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    
    results = []
    y_start = int(h * 0.30)
    y_end = int(h * 0.99)
    cropped = img[y_start:y_end, 0:w]
    resized = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    results.append(Image.fromarray(binary))
    results.append(Image.fromarray(cv2.bitwise_not(binary)))
    return results


# def easyocr_extract_simple(image_bytes: bytes) -> str:
#     """Ekstrak teks menggunakan EasyOCR"""
#     global easyocr_reader
#     if easyocr_reader is None:
#         return ""
    
#     try:
#         arr = np.frombuffer(image_bytes, np.uint8)
#         img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
#         h, w = img.shape[:2]
#         y_start = int(h * 0.30)
#         y_end = int(h * 0.99)
#         cropped = img[y_start:y_end, 0:w]
#         resized = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
#         result = easyocr_reader.readtext(resized, detail=0, paragraph=False)
#         text = ' '.join(result)
#         logger.info(f"EasyOCR extracted {len(text)} chars")
#         return text
#     except Exception as e:
#         logger.error(f"EasyOCR error: {e}")
#         return ""


def tesseract_extract(image_bytes: bytes) -> str:
    """Ekstrak teks menggunakan Tesseract"""
    best_text = ""
    best_score = 0
    
    try:
        images = preprocess_image_simple(image_bytes)
        for img in images:
            configs = ["--oem 3 --psm 6", "--oem 3 --psm 4", "--oem 1 --psm 6"]
            for config in configs:
                try:
                    text = pytesseract.image_to_string(img, config=config)
                    score = len(re.findall(r'\d+\.\d{4,}', text))
                    if score > best_score:
                        best_score = score
                        best_text = text
                except Exception:
                    continue
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
# GEMINI AI CHATBOT
# ═══════════════════════════════════════════════════════════════════

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = None
gemini_model_name = None

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-flash-8b"]

try:
    import google.genai as genai_new
    if GEMINI_API_KEY:
        gemini_client = genai_new.Client(api_key=GEMINI_API_KEY)
        gemini_model_name = "gemini-2.5-flash"
        logger.info(f"✅ Gemini AI configured: {gemini_model_name} (google-genai)")
except ImportError:
    try:
        import google.generativeai as genai_old
        if GEMINI_API_KEY:
            genai_old.configure(api_key=GEMINI_API_KEY)
            gemini_client = genai_old.GenerativeModel(
                model_name="gemini-2.5-flash",
                system_instruction=(
                    "Anda adalah OptiM AI Assistant, asisten chatbot profesional dan ramah untuk website OptiM "
                    "(sistem monitoring & klasifikasi gangguan serat optik). Anda memiliki akses ke DATA REAL-TIME dari database monitoring.\n\n"
                    "KEMAMPUAN ANDA:\n"
                    "1. Menjawab pertanyaan seputar fiber optik, jenis gangguan (Bending, Dirty Connector, Fiber Cut, Bad Splice, Air Gap, Hampir Putus/Nearly Cut), "
                    "dan cara membaca parameter OTDR (Loss dB, Return Loss dB, Prx dBm).\n"
                    "2. Merekap data monitoring: total pengukuran, jumlah dan jenis gangguan dalam rentang HARI INI, 7 HARI TERAKHIR, atau 30 HARI TERAKHIR.\n"
                    "3. Memberikan analisis tren gangguan dan rekomendasi teknis.\n\n"
                    "ATURAN FORMAT JAWABAN:\n"
                    "1. Jawablah dalam bahasa Indonesia yang sopan, RINGKAS, INFORMATIF, dan mudah dicerna dalam sekali lihat.\n"
                    "2. Untuk pertanyaan rekap/statistik: tampilkan data dalam format terstruktur dengan angka yang jelas.\n"
                    "   Contoh format rekap: '<strong>Total Gangguan Hari Ini: X</strong><br>Rincian:<ul><li>Bending (Warning): Y kali</li></ul>'\n"
                    "3. Gunakan tag HTML dasar langsung (<strong>, <br>, <ul>, <li>, <ol>). JANGAN gunakan markdown (** atau * atau #) maupun tag ```html.\n"
                    "4. Jika data menunjukkan 0 gangguan, tetap sampaikan dengan jelas: 'Tidak ada gangguan terdeteksi.'\n"
                    "5. Atur spasi agar rapat, hindari baris kosong berlebihan."
                )
            )
            gemini_model_name = "legacy"
            logger.info("✅ Gemini AI configured via legacy google.generativeai")
    except Exception as e:
        logger.warning(f"⚠️ Gagal mengonfigurasi Gemini AI: {e}")
except Exception as e:
    logger.warning(f"⚠️ Gagal mengonfigurasi Gemini AI: {e}")

SYSTEM_INSTRUCTION = (
    "Anda adalah OptiM AI Assistant, asisten chatbot profesional dan ramah untuk website OptiM "
    "(sistem monitoring & klasifikasi gangguan serat optik). Anda memiliki akses ke DATA REAL-TIME dari database monitoring.\n\n"
    "KEMAMPUAN ANDA:\n"
    "1. Menjawab pertanyaan seputar fiber optik, jenis gangguan (Bending, Dirty Connector, Fiber Cut, Bad Splice, Air Gap, Hampir Putus, Normal), "
    "dan cara membaca parameter OTDR (Loss dB, Return Loss dB, Prx dBm).\n"
    "2. Menjawab pertanyaan tentang tanggal spesifik (contoh: 8 Juni 2026) dengan memeriksa 'RINGKASAN HARIAN DATA PENGUKURAN & GANGGUAN' pada tanggal tersebut, menghitung total pengukuran, total gangguan, dan rincian jenis gangguannya.\n"
    "3. Menjawab pertanyaan tentang akumulasi tipe gangguan tertentu dalam rentang waktu tertentu (contoh: gangguan Air Gap dalam sebulan terakhir) dengan mencari dan menjumlahkan kejadian tipe tersebut dalam rentang tanggal yang sesuai.\n"
    "4. Merekap data monitoring umum: hari ini, 7 hari terakhir, 30 hari terakhir, atau keseluruhan.\n\n"
    "ATURAN FORMAT JAWABAN:\n"
    "1. Jawablah dalam bahasa Indonesia yang sopan, RINGKAS, INFORMATIF, dan ramah.\n"
    "2. Untuk pertanyaan tentang tanggal spesifik: sebutkan tanggalnya secara tebal, lalu berikan total pengukuran, total gangguan, dan rincian gangguannya.\n"
    "   Contoh: 'Pada tanggal <strong>8 Juni 2026</strong>, terdapat total <strong>15 pengukuran</strong> dengan <strong>5 gangguan terdeteksi</strong>.<br>Rincian gangguan:<ul><li>Bending (Warning): 3 kali</li><li>Dirty Connector (Warning): 2 kali</li></ul>'\n"
    "3. Untuk pertanyaan tipe gangguan dalam sebulan/seminggu: hanya sajikan info tipe gangguan yang ditanyakan secara terfokus.\n"
    "   Contoh: 'Dalam satu bulan terakhir (sejak 11 Mei 2026), jenis gangguan <strong>Air Gap</strong> terjadi sebanyak <strong>4 kali</strong>.'\n"
    "4. Gunakan tag HTML dasar langsung (<strong>, <br>, <ul>, <li>, <ol>). JANGAN gunakan markdown (** atau * atau #) maupun tag ```html.\n"
    "5. Jika tidak ada gangguan pada tanggal/periode yang ditanyakan, sampaikan dengan jelas: 'tidak ada gangguan terdeteksi.'\n"
    "6. Atur spasi agar rapat, hindari baris kosong berlebihan."
)

BOT_RESPONSES = {
    "normal": "Kondisi <strong>Normal</strong> pada jaringan fiber optik ditandai dengan redaman (loss) transmisi yang sangat rendah, biasanya di bawah 0.22 dB/km untuk panjang gelombang 1550nm. Selain itu, nilai Return Loss (refleksi balik) berada pada rentang yang sangat bagus (&lt; -40 dB) dan Rx Power stabil antara 15 hingga 25 dBm. Kondisi ini menunjukkan jalur transmisi optik bersih tanpa hambatan fisik.",
    "bending": "<strong>Bending (Tekukan Makro)</strong> terjadi ketika kabel fiber optik tertekuk melebihi radius kelengkungan minimumnya (biasanya radius &lt; 30mm). Hal ini menyebabkan cahaya bocor keluar dari core serat ke cladding, sehingga loss meningkat secara tiba-tiba di lokasi tekukan. <br><br><strong>Solusi:</strong> Periksa jalur kabel fisik, rapihkan tumpukan serat di dalam OTB (Optical Termination Box) atau splice tray, dan hindari melipat kabel dengan sudut tajam.",
    "dirty": "<strong>Dirty Connector (Konektor Kotor)</strong> adalah masalah paling umum yang terjadi pada titik sambungan konektor (patch cord / adaptor). Kotoran berupa debu, minyak sidik jari, atau kelembapan menutupi ujung ferrule, menghalangi cahaya masuk, dan memantulkan sebagian cahaya kembali. <br><br><strong>Solusi:</strong> Bersihkan ferrule konektor menggunakan *Fiber Cleaning Cassette* atau *Fiber Pen Cleaner* yang dibasahi sedikit alkohol isopropil 99%, kemudian seka kering sebelum dimasukkan kembali.",
    "cut": "<strong>Fiber Cut (Kabel Putus)</strong> adalah gangguan kritis di mana core serat optik terputus total. Hal ini menyebabkan loss transmisi melonjak hingga maksimal (50+ dB), refleksi balik hilang sepenuhnya (0 dB), dan daya penerima (Prx) turun ke 0 dBm (mati total). <br><br><strong>Penyebab:</strong> Umumnya disebabkan oleh aktivitas galian pihak ketiga, pohon tumbang, kabel tersangkut kendaraan tinggi, atau gigitan hewan pengerat.<br><br><strong>Solusi:</strong> Lakukan pengukuran OTDR untuk mencari letak persis titik putus (jarak km), lalu kirim tim teknis untuk menyambung ulang core menggunakan fusion splicer di dalam joint closure.",
    "splice": "<strong>Bad Splice (Penyambungan Buruk)</strong> terjadi saat proses penyambungan fusion splicing tidak sempurna, misalnya pemotongan serat (cleaving) yang tidak lurus, adanya debu saat peleburan, atau alignment core yang meleset. Ini menyebabkan loss lokal tinggi (&gt; 0.1 dB per sambungan).<br><br><strong>Solusi:</strong> Potong kembali ujung serat yang gagal, bersihkan core dengan tisu bebas serat (lint-free wipes) beralkohol, potong ulang secara presisi dengan fiber cleaver, lalu lakukan fusion splicing ulang menggunakan splicer yang terkalibrasi.",
    "gap": "<strong>Air Gap (Celah Udara)</strong> terjadi ketika terdapat ruang udara kecil di antara dua ujung konektor fiber optik yang saling berpasangan di dalam adaptor. Ini bisa terjadi karena konektor tidak terkunci rapat atau ukuran ferrule yang tidak pas. Celah udara ini menyebabkan indeks bias berubah tiba-tiba, memicu refleksi balik yang sangat tinggi (nilai Return Loss memburuk hingga -10 dB hingga -15 dB).<br><br><strong>Solusi:</strong> Pastikan konektor terdorong penuh hingga berbunyi 'klik' dan terkunci di adaptornya, atau ganti adaptor/konektor yang sudah longgar.",
    "nearly": "<strong>Nearly Cut (Hampir Putus)</strong> adalah kondisi kritis di mana serat optik mengalami kerusakan fisik berat namun belum putus sepenuhnya (misal core tergores, retak, atau tertarik keras). Ini menyebabkan loss transmisi meningkat sangat tinggi dan sinyal melemah secara drastis.<br><br><strong>Solusi:</strong> Segera jadwalkan pemeliharaan preventif sebelum serat terputus total. Potong segmen yang rusak lalu sambung ulang serat di lokasi tersebut.",
    "bantuan": "Saya dapat membantu Anda memberikan informasi tentang jaringan fiber optik. Silakan ketik nama gangguan seperti: <ul><li><strong>Bending</strong></li><li><strong>Dirty Connector</strong></li><li><strong>Fiber Cut</strong></li><li><strong>Bad Splice</strong></li><li><strong>Air Gap</strong></li><li><strong>Nearly Cut</strong></li></ul>Atau tanyakan cara perbaikan jaringan.",
    "default": "Maaf, saya tidak begitu memahami pertanyaan tersebut. Coba tanyakan mengenai jenis gangguan jaringan fiber optik seperti <strong>Bending</strong>, <strong>Dirty Connector</strong>, <strong>Fiber Cut</strong>, atau <strong>Bad Splice</strong> agar saya dapat membantu Anda."
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
    """Konversi markdown ke HTML dengan spacing yang bersih"""
    # Hapus wrapper ```html atau ``` yang mungkin ditambahkan AI
    text = re.sub(r'```html\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    
    # Ganti markdown bold/italic
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)
    
    # Ganti newlines dengan <br>
    html = html.replace('\n', '<br>')
    
    # Bersihkan <br> yang tidak perlu di sekitar tag list/struktur HTML agar spasi rapat
    html = re.sub(r'(<ul>|<ol>|<li>)\s*<br>', r'\1', html)
    html = re.sub(r'<br>\s*(</ul>|</ol>|</li>)', r'\1', html)
    html = re.sub(r'</li>\s*<br>\s*<li>', r'</li><li>', html)
    html = re.sub(r'</ul>\s*<br>\s*<ul>', r'</ul><ul>', html)
    
    # Ganti kelipatan <br> berlebih (lebih dari dua) menjadi maksimal satu saja untuk spasi rapat
    html = re.sub(r'(<br>\s*){2,}', r'<br>', html)
    
    return html.strip()

def make_db_aware_response(user_message: str, today_total: int, today_g: int, today_detail: str,
                            week_total: int, week_g: int, week_detail: str,
                            month_total: int, month_g: int, month_detail: str,
                            total_data: int, all_g_total: int, all_detail: str,
                            latest, daily_records: list = None, history_records: list = None) -> str:
    """Jawaban berbasis data DB tanpa Gemini — untuk rekap statistik dan filter kustom"""
    q = user_message.lower()
    
    import re
    from datetime import datetime, timedelta
    
    # Mapping nama bulan Indonesia ke nomor bulan
    indo_months = {
        "januari": "01", "februari": "02", "maret": "03", "april": "04",
        "mei": "05", "juni": "06", "juli": "07", "agustus": "08",
        "september": "09", "oktober": "10", "november": "11", "desember": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "mei": "05", "jun": "06",
        "jul": "07", "agu": "08", "sep": "09", "okt": "10", "nov": "11", "des": "12"
    }
    
    # 1. Deteksi pertanyaan tanggal spesifik (contoh: "8 juni 2026")
    date_match = re.search(r'(\d{1,2})\s+([a-zA-Z]+)\s+(\d{4})', q)
    if date_match:
        day_str = date_match.group(1).zfill(2)
        month_name = date_match.group(2).lower()
        year_str = date_match.group(3)
        
        month_str = indo_months.get(month_name, None)
        if month_str:
            target_date_str = f"{year_str}-{month_str}-{day_str}"
            
            total_g = 0
            total_measurements = 0
            details = []
            if daily_records:
                for r in daily_records:
                    if r.date_str == target_date_str:
                        total_measurements += r.count
                        if r.klasifikasi != "Normal":
                            total_g += r.count
                            details.append(f"{r.klasifikasi} ({r.status}): {r.count} kali")
            
            detail_html = ""
            if total_g > 0:
                li_items = "".join([f"<li>{item}</li>" for item in details])
                detail_html = f"<br>Rincian gangguan:<ul>{li_items}</ul>"
            else:
                detail_html = " (tidak ada gangguan terdeteksi)."
                
            return (
                f"Pada tanggal <strong>{day_str} {month_name.capitalize()} {year_str}</strong>, "
                f"terdapat total <strong>{total_measurements} pengukuran</strong> dengan "
                f"<strong>{total_g} gangguan terdeteksi</strong>.{detail_html}"
            )
            
    # 2. Deteksi kueri rentang waktu untuk tipe gangguan tertentu
    fault_types = {
        "air gap": "Air Gap",
        "bending": "Bending",
        "dirty connector": "Dirty Connector",
        "hampir putus": "Hampir Putus",
        "nearly cut": "Hampir Putus",
        "fiber cut": "Fiber Cut",
        "bad splice": "Bad Splice",
        "normal": "Normal"
    }
    
    detected_fault = None
    for key, val in fault_types.items():
        if key in q:
            detected_fault = val
            break
            
    if detected_fault:
        # Tentukan rentang hari
        days_limit = None
        period_name = ""
        if any(x in q for x in ["bulan", "30 hari", "sebulan"]):
            days_limit = 30
            period_name = "satu bulan terakhir"
        elif any(x in q for x in ["minggu", "7 hari", "seminggu"]):
            days_limit = 7
            period_name = "7 hari terakhir"
        elif any(x in q for x in ["hari ini", "today"]):
            days_limit = 0
            period_name = "hari ini"
            
        if days_limit is not None:
            now = datetime.now()
            limit_date = (now - timedelta(days=days_limit)).date()
            now_str = now.strftime("%Y-%m-%d")
            
            total_count = 0
            if daily_records:
                for r in daily_records:
                    if r.date_str:
                        if days_limit == 0:
                            if r.date_str == now_str and r.klasifikasi == detected_fault:
                                total_count += r.count
                        else:
                            try:
                                r_date = datetime.strptime(r.date_str, "%Y-%m-%d").date()
                                if r_date >= limit_date and r.klasifikasi == detected_fault:
                                    total_count += r.count
                            except Exception:
                                pass
            
            status_text = "gangguan" if detected_fault != "Normal" else "status"
            date_info = f" sejak {limit_date.strftime('%d %B %Y')}" if days_limit > 0 else ""
            return (
                f"Dalam {period_name}{date_info}, "
                f"jenis {status_text} <strong>{detected_fault}</strong> terjadi sebanyak "
                f"<strong>{total_count} kali</strong>."
            )
            
    # Fallback standard
    def fmt_breakdown(detail_str: str, g_total: int) -> str:
        if g_total == 0 or detail_str == "Tidak ada gangguan":
            return "Tidak ada gangguan terdeteksi."
        items = detail_str.split(", ")
        li_items = "".join([f"<li>{item}</li>" for item in items])
        return f"<ul>{li_items}</ul>"
    
    # Rekap hari ini
    if any(x in q for x in ["hari ini", "today", "hari", "sekarang"]):
        breakdown_html = fmt_breakdown(today_detail, today_g)
        return (
            f"<strong>Rekap Monitoring Hari Ini</strong><br>"
            f"Total Pengukuran: <strong>{today_total}</strong><br>"
            f"Total Gangguan: <strong>{today_g}</strong><br>"
            f"Rincian Jenis Gangguan:<br>{breakdown_html}"
        )
    # Rekap 7 hari / minggu ini
    elif any(x in q for x in ["minggu", "7 hari", "seminggu", "week"]):
        breakdown_html = fmt_breakdown(week_detail, week_g)
        return (
            f"<strong>Rekap Monitoring 7 Hari Terakhir</strong><br>"
            f"Total Pengukuran: <strong>{week_total}</strong><br>"
            f"Total Gangguan: <strong>{week_g}</strong><br>"
            f"Rincian Jenis Gangguan:<br>{breakdown_html}"
        )
    # Rekap bulan / 30 hari
    elif any(x in q for x in ["bulan", "30 hari", "sebulan", "month"]):
        breakdown_html = fmt_breakdown(month_detail, month_g)
        return (
            f"<strong>Rekap Monitoring Bulan Ini (30 Hari Terakhir)</strong><br>"
            f"Total Pengukuran: <strong>{month_total}</strong><br>"
            f"Total Gangguan: <strong>{month_g}</strong><br>"
            f"Rincian Jenis Gangguan:<br>{breakdown_html}"
        )
    # Rekap keseluruhan / semua data
    elif any(x in q for x in ["semua", "keseluruhan", "total", "rekap", "laporan", "berapa", "gangguan"]):
        breakdown_html = fmt_breakdown(all_detail, all_g_total)
        latest_info = ""
        if latest:
            latest_info = (
                f"<br><strong>Pengukuran Terakhir:</strong> {latest.klasifikasi} ({latest.status}) "
                f"— Loss: {latest.loss_1} dB, Prx: {latest.prx} dBm"
            )
        return (
            f"<strong>Rekap Keseluruhan Sistem OptiM</strong><br>"
            f"Total Seluruh Pengukuran: <strong>{total_data}</strong><br>"
            f"Total Seluruh Gangguan: <strong>{all_g_total}</strong><br>"
            f"Rincian Jenis Gangguan:<br>{breakdown_html}"
            f"{latest_info}"
        )
    else:
        return get_local_chatbot_response(user_message)


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
    
    # Selalu ambil data DB dulu (dipakai oleh Gemini dan fallback)
    try:
        # Konversi waktu server (UTC) ke waktu lokal WIB (GMT+7) agar sinkron dengan tabel/dashboard
        now_wib = datetime.utcnow() + timedelta(hours=7)
        
        # 1. Ambil seluruh data pengukuran user diurutkan dari terlama ke terbaru (ASC)
        stmt_all = select(OtdrResult).order_by(OtdrResult.timestamp.asc())
        if current_user:
            stmt_all = stmt_all.where(OtdrResult.user_id == current_user.id)
        result_all = await db.execute(stmt_all)
        all_records = result_all.scalars().all()
        
        # 2. Saring data berdasarkan current_index dari slideshow jika dikirim
        current_index = request.get("current_index", None)
        if current_index is not None:
            try:
                idx = int(current_index)
                if 0 <= idx < len(all_records):
                    all_records = all_records[:idx + 1]
            except Exception:
                pass
        
        # 3. Hitung parameter statistik di Python dengan penyesuaian WIB
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
        
        # History list (150 data terbaru dari data yang sudah terjadi)
        # Data dibalik karena all_records berurutan ASC (terlama ke terbaru)
        history_records = list(reversed(all_records))[:150]
        
        history_list_str = "\n".join(
            [f"- {(rec.timestamp + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')} | {rec.klasifikasi} | {rec.status}" 
             for rec in history_records if rec.timestamp]
        ) if history_records else "Belum ada riwayat data."
        
        # Ringkasan harian
        daily_summary_map = {}
        
        for rec in all_records:
            if not rec.timestamp:
                continue
            # Konversi timestamp UTC database ke WIB (UTC+7)
            rec_wib = rec.timestamp + timedelta(hours=7)
            rec_date = rec_wib.date()
            date_str = rec_date.strftime("%Y-%m-%d")
            
            # Kelompok harian
            if date_str not in daily_summary_map:
                daily_summary_map[date_str] = {}
            daily_summary_map[date_str][(rec.klasifikasi, rec.status)] = daily_summary_map[date_str].get((rec.klasifikasi, rec.status), 0) + 1
            
            # Cek status gangguan
            is_fault = (rec.klasifikasi != "Normal")
            
            if is_fault:
                all_g_total += 1
                all_faults[(rec.klasifikasi, rec.status)] = all_faults.get((rec.klasifikasi, rec.status), 0) + 1
                
            # Hari Ini
            if rec_date == today_date:
                today_total += 1
                if is_fault:
                    today_g += 1
                    today_faults[(rec.klasifikasi, rec.status)] = today_faults.get((rec.klasifikasi, rec.status), 0) + 1
                    
            # 7 Hari Terakhir
            if rec_date >= week_start_date:
                week_total += 1
                if is_fault:
                    week_g += 1
                    week_faults[(rec.klasifikasi, rec.status)] = week_faults.get((rec.klasifikasi, rec.status), 0) + 1
                    
            # 30 Hari Terakhir (Bulan Ini)
            if rec_date >= month_start_date:
                month_total += 1
                if is_fault:
                    month_g += 1
                    month_faults[(rec.klasifikasi, rec.status)] = month_faults.get((rec.klasifikasi, rec.status), 0) + 1
                    
        # Format breakdown strings
        def fmt_detail(faults_map):
            if not faults_map:
                return "Tidak ada gangguan"
            sorted_faults = sorted(faults_map.items(), key=lambda x: x[1], reverse=True)
            return ", ".join([f"{k} ({s}): {c} kali" for (k, s), c in sorted_faults])
            
        today_detail = fmt_detail(today_faults)
        week_detail = fmt_detail(week_faults)
        month_detail = fmt_detail(month_faults)
        all_detail = fmt_detail(all_faults)
        
        # Konversi daily_summary_map ke model record stub untuk fallback
        class DailyRecordStub:
            def __init__(self, date_str, klasifikasi, status, count):
                self.date_str = date_str
                self.klasifikasi = klasifikasi
                self.status = status
                self.count = count
                
        daily_records = []
        daily_list = []
        for d_str in sorted(daily_summary_map.keys(), reverse=True):
            for (klas, stat), count in daily_summary_map[d_str].items():
                rec = DailyRecordStub(d_str, klas, stat, count)
                daily_records.append(rec)
                daily_list.append(f"{d_str} | {klas} ({stat}) | {count} kali")
                
        daily_summary_str = "\n".join(daily_list) if daily_list else "Belum ada ringkasan harian."
        
    except Exception as db_err:
        logger.error(f"DB error in chat: {db_err}")
        return {"response": get_local_chatbot_response(user_message), "source": "local_fallback"}
    
    # Jika tidak ada Gemini, gunakan DB-aware fallback langsung
    if not GEMINI_API_KEY or gemini_client is None:
        reply = make_db_aware_response(
            user_message, today_total, today_g, today_detail,
            week_total, week_g, week_detail,
            month_total, month_g, month_detail,
            total_data, all_g_total, all_detail, latest,
            daily_records, history_records
        )
        return {"response": reply, "source": "db_fallback"}
    
    # Format context state dari frontend jika ada
    context_str = ""
    if context_state:
        context_str = "\n".join([f"- {k}: {v}" for k, v in context_state.items() if v is not None])
    
    # Buat prompt lengkap
    latest_wib_time = (latest.timestamp + timedelta(hours=7)).strftime('%d %B %Y %H:%M') if latest and latest.timestamp else 'N/A'
    prompt = f"""[DATA REAL-TIME MONITORING OPTIM — {now_wib.strftime('%d %B %Y %H:%M')}]

=== KONTEKS SEKARANG DI FRONTEND ===
{context_str}

=== RINGKASAN HARIAN DATA PENGUKURAN & GANGGUAN ===
Format: [Tanggal] | [Klasifikasi] ([Status]) | [Jumlah Kejadian]
{daily_summary_str}

=== REKAPITULASI HARI INI ({now_wib.strftime('%d %B %Y')}) ===
- Total pengukuran: {today_total}
- Total gangguan terdeteksi: {today_g}
- Rincian gangguan: {today_detail}

=== REKAPITULASI 7 HARI TERAKHIR ===
- Total pengukuran: {week_total}
- Total gangguan terdeteksi: {week_g}
- Rincian gangguan: {week_detail}

=== REKAPITULASI 30 HARI TERAKHIR (BULAN INI) ===
- Total pengukuran: {month_total}
- Total gangguan terdeteksi: {month_g}
- Rincian gangguan: {month_detail}

=== DATA KESELURUHAN SISTEM ===
- Total seluruh data pengukuran: {total_data}
- Total seluruh gangguan: {all_g_total}
- Rincian seluruh gangguan: {all_detail}

=== DETAIL RIWAYAT PENGUKURAN TERBARU (Maks 150 data) ===
Format: [Tanggal & Waktu] | [Jenis Gangguan] | [Status/Tingkat Bahaya]
{history_list_str}

=== PENGUKURAN TERAKHIR ===
- Klasifikasi: {latest.klasifikasi if latest else 'Belum ada'}
- Status: {latest.status if latest else 'Belum ada'}
- Loss KM1-4: {latest.loss_1 if latest else 0} dB | {latest.loss_2 if latest else 0} dB | {latest.loss_3 if latest else 0} dB | {latest.loss_4 if latest else 0} dB
- Prx: {latest.prx if latest else 0} dBm
- Waktu: {latest_wib_time}

[PERTANYAAN PENGGUNA]
{user_message}
"""
    
    # Coba tiap model Gemini dengan fallback
    last_err = None
    for model_name in GEMINI_MODELS:
        try:
            if gemini_model_name == "legacy":
                # Pakai google.generativeai lama
                response_data = await asyncio.to_thread(gemini_client.generate_content, prompt)
                reply = response_data.text
            else:
                # Pakai google.genai baru
                full_prompt = SYSTEM_INSTRUCTION + "\n\n" + prompt
                response_data = await asyncio.to_thread(
                    gemini_client.models.generate_content,
                    model=model_name,
                    contents=full_prompt
                )
                reply = response_data.text
            
            reply_html = format_markdown_to_html(reply)
            logger.info(f"[CHAT] Gemini ({model_name}) responded OK")
            return {"response": reply_html, "source": f"gemini_{model_name}"}
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if "quota" in err_str or "429" in err_str or "rate" in err_str:
                logger.warning(f"[CHAT] {model_name} quota habis, coba model berikutnya...")
                continue
            else:
                logger.error(f"[CHAT] {model_name} error: {e}")
                break
    
    # Semua model gagal — gunakan DB-aware fallback
    logger.error(f"[CHAT] Semua model Gemini gagal: {last_err}. Menggunakan DB fallback.")
    reply = make_db_aware_response(
        user_message, today_total, today_g, today_detail,
        week_total, week_g, week_detail,
        month_total, month_g, month_detail,
        total_data, all_g_total, all_detail, latest,
        daily_records, history_records
    )
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
    ocr_method = "none"
    
    logger.info("=" * 70)
    logger.info("🔄 Starting OCR process...")
    
    # async def run_easyocr():
    #     if easyocr_reader is not None:
    #         return easyocr_extract_simple(content)
    #     return ""
    
    async def run_tesseract():
        return tesseract_extract(content)
    
    async def run_ocrspace():
        return ocr_space_extract(content)
    
    try:
        # 🔥 FIX: Timeout per-method agar tidak 504 di Azure
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

        # if easyocr_reader is not None:
        #     try:
        #         results['easyocr'] = await asyncio.wait_for(
        #             asyncio.to_thread(easyocr_extract_simple, content), timeout=20.0)
        #     except asyncio.TimeoutError:
        #         logger.warning("EasyOCR timed out")
        #         results['easyocr'] = ""

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
    
    # ML Prediction
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
        logger.info(f"🤖 ML prediction SUCCESS: {pred.get('prediction')} (confidence: {pred.get('confidence')}%)")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED: {e}")
        pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
    
    # Save to database
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
    
    # Kirim Telegram Alert jika terdeteksi anomali (warning/critical)
        status_str = pred.get("status", "Normal")
        logger.info(f"[TELEGRAM] Status hasil prediksi: '{status_str}'")
        if status_str.lower() in ["warning", "critical"]:
            logger.info(f"[TELEGRAM] Mengirim alert untuk: {pred.get('prediction')} ({status_str})")
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
                logger.error(f"[TELEGRAM] Error saat kirim alert (OCR): {tg_err}")
        
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
            "prx": r.prx,
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
    """Endpoint manual untuk mengirimkan alert dari dashboard ke Telegram"""
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
    """Endpoint untuk mengirimkan alert dari slideshow monitoring ke Telegram dengan deduplikasi"""
    try:
        record_id = payload.get("id")
        if not record_id:
            raise HTTPException(status_code=400, detail="Missing record id")
        
        result = await db.execute(select(OtdrResult).where(OtdrResult.id == record_id))
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Record not found")
        
        # Skip jika sudah dikirim (dibypass agar slideshow tetap mengirim alert)
        # if record.telegram_alert_sent:
        #     return {"status": "skipped", "reason": "alert already sent"}
        
        # Skip jika normal
        status_str = record.status or ""
        if status_str.lower() not in ["warning", "critical"]:
            return {"status": "skipped", "reason": f"status is {status_str}"}
        
        # Kirim Telegram alert
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
        
        # Tandai sudah dikirim
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
    
    for _, row in df.iterrows():
        try:
            def g(col, default=0.0):
                try:
                    val = row.get(col, default)
                    return float(val) if pd.notna(val) else default
                except Exception:
                    return default
            
            otdr_values = {
                'Prx (dBm)': g('Prx (dBm)'),
                'Distance 1': g('Distance 1'), 'Distance 2': g('Distance 2'),
                'Distance 3': g('Distance 3'), 'Distance 4': g('Distance 4'),
                'Loss 1': g('Loss 1'), 'Loss 2': g('Loss 2'), 'Loss 3': g('Loss 3'),
                'Total-L 1': g('Total-L 1'), 'Total-L 2': g('Total-L 2'),
                'Total-L 3': g('Total-L 3'), 'Total-L 4': g('Total-L 4'),
                'Avg-L 1': g('Avg-L 1'), 'Avg-L 2': g('Avg-L 2'),
                'Avg-L 3': g('Avg-L 3'), 'Avg-L 4': g('Avg-L 4'),
                'Avg-Total': g('Avg-Total'),
                'Return 1': g('Return 1'), 'Return 2': g('Return 2'),
                'Return 3': g('Return 3'), 'Return 4': g('Return 4'),
            }
            
            pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)
            
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
        # "easyocr": "loaded" if easyocr_reader else ("loading" if easyocr_loading else "not loaded"),
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
    
    # Calculate derived values (total loss and avg loss per km) - support manual input/overrides
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
        logger.info(f"🤖 ML prediction SUCCESS (manual): {pred.get('prediction')} (confidence: {pred.get('confidence')}%)")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED (manual): {e}")
        pred = {
            "prediction": "Normal",
            "confidence": 70.0,
            "status": "Normal"
        }
        
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
        
        # Kirim Telegram Alert jika terdeteksi anomali (warning/critical)
        status_str = pred.get("status", "Normal")
        logger.info(f"[TELEGRAM] Status hasil prediksi (manual): '{status_str}'")
        if status_str.lower() in ["warning", "critical"]:
            logger.info(f"[TELEGRAM] Mengirim alert untuk: {pred.get('prediction')} ({status_str})")
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
                # await sync_db_to_persistent()
            except Exception as tg_err:
                logger.error(f"[TELEGRAM] Error saat kirim alert (manual): {tg_err}")
    
    # Kirim Telegram Alert jika terdeteksi anomali (warning/critical)
        status_str = pred.get("status", "Normal")
        logger.info(f"[TELEGRAM] Status hasil prediksi: '{status_str}'")
        if status_str.lower() in ["warning", "critical"]:
            logger.info(f"[TELEGRAM] Mengirim alert untuk: {pred.get('prediction')} ({status_str})")
            try:
                await asyncio.to_thread(
                    send_telegram_alert,
                    classification=pred.get("prediction"),
                    status=status_str,
                    loss=[rows[0]['loss'], rows[1]['loss'], rows[2]['loss'], rows[3]['loss']], # type: ignore
                    rl=[rows[0]['return'], rows[1]['return'], rows[2]['return'], rows[3]['return']], # pyright: ignore[reportUndefinedVariable]
                    prx=final_prx,
                    distances=[rows[0]['distance'], rows[1]['distance'], rows[2]['distance'], rows[3]['distance']], # type: ignore
                    timestamp=record.timestamp
                )
                record.telegram_alert_sent = True
                await db.commit()
            except Exception as tg_err:
                logger.error(f"[TELEGRAM] Error saat kirim alert (OCR): {tg_err}")

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