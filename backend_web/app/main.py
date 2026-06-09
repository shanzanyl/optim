# main.py - VERSION FIXED (LENGKAP)
from datetime import datetime
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
easyocr_reader = None
easyocr_loading = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global easyocr_reader, easyocr_loading
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
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
    
    # 🔥 Load EasyOCR di background (non-blocking)
    async def load_easyocr_background():
        global easyocr_reader, easyocr_loading
        easyocr_loading = True
        try:
            def _init_reader():
                import easyocr
                return easyocr.Reader(['en'], gpu=False, verbose=False)
            
            easyocr_reader = await asyncio.to_thread(_init_reader)
            logger.info("✅ EasyOCR loaded successfully")
        except Exception as e:
            logger.warning(f"⚠️ EasyOCR failed to load: {e}")
            easyocr_reader = None
        finally:
            easyocr_loading = False
    
    asyncio.create_task(load_easyocr_background())
    logger.info("🔄 EasyOCR loading started in background...")
    
    # 🔥 Auto sync background task (every 6 hours)
    async def auto_sync_sheets():
        # 🔥 FIX: Sync langsung saat startup, tidak perlu tunggu 6 jam
        await asyncio.sleep(10)
        while True:
            try:
                logger.info("🔄 Auto-sync from Google Sheets...")
                async with AsyncSessionLocal() as db_sync:
                    # Get all users
                    result = await db_sync.execute(select(User))
                    users = result.scalars().all()
                    
                    for user in users:
                        try:
                            async with httpx.AsyncClient() as client:
                                resp = await client.get(SHEET_URL, timeout=30)
                                if resp.status_code == 200:
                                    df = pd.read_csv(io.StringIO(resp.text))
                                    df.columns = [c.strip() for c in df.columns]
                                    
                                    # Delete old sheets data
                                    existing = await db_sync.execute(
                                        select(OtdrResult).where(
                                            OtdrResult.user_id == user.id,
                                            OtdrResult.source == "sheets",
                                        )
                                    )
                                    for rec in existing.scalars().all():
                                        await db_sync.delete(rec)
                                    await db_sync.flush()
                                    
                                    # Insert new data
                                    for _, row in df.iterrows():
                                        def g(col, default=0.0):
                                            try:
                                                val = row.get(col, default)
                                                return float(val) if pd.notna(val) else default
                                            except Exception:
                                                return default
                                        
                                        otdr_values = {
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
                                            user_id=user.id,
                                            timestamp=datetime.now(),
                                            prx=g('Prx (dBm)'),
                                            temperature=g('Temperature (C)'),
                                            wavelength=g('Wavelength'),
                                            pulse_width=g('Pulse Width (ns)'),
                                            distance_1=g('Distance 1'), distance_2=g('Distance 2'),
                                            distance_3=g('Distance 3'), distance_4=g('Distance 4'),
                                            loss_1=g('Loss 1'), loss_2=g('Loss 2'), loss_3=g('Loss 3'), loss_4=g('Loss 4'),
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
                                        db_sync.add(record)
                                    
                                    await db_sync.commit()
                                    logger.info(f"Auto-sync success for {user.email}")
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


def easyocr_extract_simple(image_bytes: bytes) -> str:
    """Ekstrak teks menggunakan EasyOCR"""
    global easyocr_reader
    if easyocr_reader is None:
        return ""
    
    try:
        arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        y_start = int(h * 0.30)
        y_end = int(h * 0.99)
        cropped = img[y_start:y_end, 0:w]
        resized = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        result = easyocr_reader.readtext(resized, detail=0, paragraph=False)
        text = ' '.join(result)
        logger.info(f"EasyOCR extracted {len(text)} chars")
        return text
    except Exception as e:
        logger.error(f"EasyOCR error: {e}")
        return ""


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

def format_markdown_to_html(text: str) -> str:
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)
    html = html.replace('\n', '<br>')
    return html


@app.post("/api/chat")
async def chat(
    request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user)
):
    user_message = request.get("message", "").strip()
    
    if not user_message:
        return {"response": "Pesan tidak boleh kosong.", "source": "error"}
    
    if not GEMINI_API_KEY:
        return {"response": "Maaf, chatbot belum tersedia.", "source": "error"}
    
    try:
        result_total = await db.execute(select(func.count(OtdrResult.id)))
        total_data = result_total.scalar() or 0
        
        result_latest = await db.execute(
            select(OtdrResult).order_by(OtdrResult.timestamp.desc()).limit(1)
        )
        latest = result_latest.scalar_one_or_none()

        # Database Stats queries for frequency of issues
        from datetime import timedelta
        now_time = datetime.now()
        seven_days_ago = now_time - timedelta(days=7)
        thirty_days_ago = now_time - timedelta(days=30)

        # 1. Stats last 7 days
        q_7d = select(OtdrResult.klasifikasi, func.count(OtdrResult.id))\
            .where(OtdrResult.klasifikasi != "Normal", OtdrResult.timestamp >= seven_days_ago)\
            .group_by(OtdrResult.klasifikasi)\
            .order_by(func.count(OtdrResult.id).desc())
        r_7d = await db.execute(q_7d)
        stats_7d = r_7d.all()

        # 2. Stats last 30 days
        q_30d = select(OtdrResult.klasifikasi, func.count(OtdrResult.id))\
            .where(OtdrResult.klasifikasi != "Normal", OtdrResult.timestamp >= thirty_days_ago)\
            .group_by(OtdrResult.klasifikasi)\
            .order_by(func.count(OtdrResult.id).desc())
        r_30d = await db.execute(q_30d)
        stats_30d = r_30d.all()

        # 3. Overall Stats
        q_all = select(OtdrResult.klasifikasi, func.count(OtdrResult.id))\
            .where(OtdrResult.klasifikasi != "Normal")\
            .group_by(OtdrResult.klasifikasi)\
            .order_by(func.count(OtdrResult.id).desc())
        r_all = await db.execute(q_all)
        stats_all = r_all.all()

        def format_stats_list(stats_list):
            if not stats_list:
                return "Tidak ada gangguan terdeteksi"
            return "<br>".join([f"• {row[0]}: <strong>{row[1]} kali</strong>" for row in stats_list])

        stats_7d_str = format_stats_list(stats_7d)
        stats_30d_str = format_stats_list(stats_30d)
        stats_all_str = format_stats_list(stats_all)
        
        prompt = f"""Anda adalah asisten AI untuk aplikasi OptiM.

[DATA REAL-TIME DARI DATABASE]
- Total data pengukuran: {total_data}
- Klasifikasi terakhir: {latest.klasifikasi if latest else 'Belum ada'}
- Status terakhir: {latest.status if latest else 'Belum ada'}
- Loss terakhir: KM1={latest.loss_1 if latest else 0} dB, KM2={latest.loss_2 if latest else 0} dB, KM3={latest.loss_3 if latest else 0} dB, KM4={latest.loss_4 if latest else 0} dB
- Prx: {latest.prx if latest else 0} dBm
- Statistik gangguan 7 hari terakhir:
{stats_7d_str}
- Statistik gangguan 30 hari terakhir:
{stats_30d_str}
- Statistik gangguan keseluruhan (all-time):
{stats_all_str}

[PERTANYAAN PENGGUNA]
{user_message}

[JAWABAN]
Jawab dengan bahasa Indonesia yang ramah dan profesional. Format output dengan HTML."""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        response = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, json=payload, timeout=15)
                    if response.status_code == 200:
                        break
                    elif response.status_code == 429:
                        logger.warning(f"Gemini API rate limited (429), retrying in {1.5 * (attempt + 1)}s...")
                        await asyncio.sleep(1.5 * (attempt + 1))
                    else:
                        break
            except Exception as e:
                logger.error(f"Gemini request attempt {attempt} failed: {e}")
                if attempt == 1:
                    break

        if response and response.status_code == 200:
            data = response.json()
            reply = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"response": format_markdown_to_html(reply), "source": "gemini_api"}
        else:
            # Smart rule-based fallback based on DB values if API fails/gets rate-limited
            msg_lower = user_message.lower()
            if any(x in msg_lower for x in ["sering", "terbanyak", "dominan", "minggu", "bulan", "mingguan", "bulanan"]):
                if "minggu" in msg_lower or "7 hari" in msg_lower:
                    reply_html = f"Berikut adalah statistik gangguan dalam 1 minggu terakhir (7 hari):<br>{stats_7d_str}"
                elif "bulan" in msg_lower or "30 hari" in msg_lower:
                    reply_html = f"Berikut adalah statistik gangguan dalam 1 bulan terakhir (30 hari):<br>{stats_30d_str}"
                else:
                    reply_html = f"Gangguan yang paling sering terjadi (keseluruhan data):<br>{stats_all_str}<br><br>Statistik 7 hari terakhir:<br>{stats_7d_str}"
            elif any(x in msg_lower for x in ["loss", "rugi", "db"]):
                if latest:
                    reply_html = f"Berdasarkan data terakhir di database:<br>• Loss KM 1: <strong>{latest.loss_1 or 0} dB</strong><br>• Loss KM 2: <strong>{latest.loss_2 or 0} dB</strong><br>• Loss KM 3: <strong>{latest.loss_3 or 0} dB</strong><br>• Loss KM 4: <strong>{latest.loss_4 or 0} dB</strong>"
                else:
                    reply_html = "Belum ada data pengukuran OTDR di database."
            elif any(x in msg_lower for x in ["gangguan", "anomali", "masalah", "klasifikasi", "status", "rusak", "putus"]):
                if latest:
                    reply_html = f"Hasil deteksi terakhir menunjukkan klasifikasi: <strong>{latest.klasifikasi or 'Normal'}</strong> dengan status: <strong>{latest.status or 'Normal'}</strong>."
                else:
                    reply_html = "Belum ada data riwayat pengukuran di database."
            elif any(x in msg_lower for x in ["prx", "daya", "signal", "sinyal"]):
                if latest:
                    reply_html = f"Nilai daya sinyal penerimaan terakhir (Prx) adalah: <strong>{latest.prx or 0} dBm</strong>."
                else:
                    reply_html = "Belum ada data daya sinyal penerimaan di database."
            elif any(x in msg_lower for x in ["total", "jumlah", "banyak", "data", "riwayat"]):
                reply_html = f"Total data pengukuran OTDR yang tersimpan saat ini sebanyak <strong>{total_data}</strong> data."
            else:
                status_str = f"klasifikasi terakhir: <strong>{latest.klasifikasi}</strong> ({latest.status})" if latest else "belum ada data"
                reply_html = f"Maaf, saat ini kuota layanan AI sedang penuh (Rate Limit 429).<br><br><strong>Informasi dari Database:</strong><br>• Total data: {total_data}<br>• Status terakhir: {status_str}"
                
            return {"response": reply_html, "source": "local_fallback"}
    except Exception as e:
        logger.error(f"Chat exception: {e}")
        return {"response": f"Maaf, terjadi kesalahan: {str(e)[:200]}", "source": "error"}

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
    
    async def run_easyocr():
        if easyocr_reader is not None:
            return easyocr_extract_simple(content)
        return ""
    
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

        if easyocr_reader is not None:
            try:
                results['easyocr'] = await asyncio.wait_for(
                    asyncio.to_thread(easyocr_extract_simple, content), timeout=20.0)
            except asyncio.TimeoutError:
                logger.warning("EasyOCR timed out")
                results['easyocr'] = ""

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
            loss_3=rows[2]['loss'], loss_4=rows[3]['loss'],
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
                loss_1=g('Loss 1'), loss_2=g('Loss 2'), loss_3=g('Loss 3'), loss_4=g('Loss 4'),
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
        "easyocr": "loaded" if easyocr_reader else ("loading" if easyocr_loading else "not loaded"),
    }


@app.post("/api/classify-manual")
async def classify_manual(
    payload: ManualClassifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    logger.info("=" * 70)
    logger.info("🔄 Starting Manual Classification...")
    
    otdr_values = {
        'Prx (dBm)': payload.prx,
        'Distance 1': payload.distance_1, 'Distance 2': payload.distance_2,
        'Distance 3': payload.distance_3, 'Distance 4': payload.distance_4,
        'Loss 1': payload.loss_1, 'Loss 2': payload.loss_2, 'Loss 3': payload.loss_3,
        'Total-L 1': payload.total_l_1, 'Total-L 2': payload.total_l_2,
        'Total-L 3': payload.total_l_3, 'Total-L 4': payload.total_l_4,
        'Avg-L 1': payload.avg_l_1, 'Avg-L 2': payload.avg_l_2,
        'Avg-L 3': payload.avg_l_3, 'Avg-L 4': payload.avg_l_4,
        'Avg-Total': payload.avg_total,
        'Return 1': payload.return_1, 'Return 2': payload.return_2,
        'Return 3': payload.return_3, 'Return 4': payload.return_4,
    }
    
    try:
        pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)
        logger.info(f"🤖 ML manual prediction SUCCESS: {pred.get('prediction')} (confidence: {pred.get('confidence')}%)")
    except Exception as e:
        logger.error(f"❌ ML manual prediction FAILED: {e}")
        pred = {"prediction": "Normal", "confidence": 70.0, "status": "Normal"}
        
    user_id = current_user.id if current_user else 1
    
    try:
        record = OtdrResult(
            user_id=user_id,
            timestamp=datetime.now(),
            prx=payload.prx,
            loss_1=payload.loss_1, loss_2=payload.loss_2,
            loss_3=payload.loss_3, loss_4=payload.loss_4,
            return_1=payload.return_1, return_2=payload.return_2,
            return_3=payload.return_3, return_4=payload.return_4,
            distance_1=payload.distance_1, distance_2=payload.distance_2,
            distance_3=payload.distance_3, distance_4=payload.distance_4,
            total_l_1=payload.total_l_1, total_l_2=payload.total_l_2,
            total_l_3=payload.total_l_3, total_l_4=payload.total_l_4,
            avg_l_1=payload.avg_l_1, avg_l_2=payload.avg_l_2,
            avg_l_3=payload.avg_l_3, avg_l_4=payload.avg_l_4,
            klasifikasi=pred.get("prediction"),
            status=pred.get("status"),
            confidence=pred.get("confidence"),
            source="manual",
            raw_text="Manual Input Classification",
        )
        
        db.add(record)
        await db.commit()
        await db.refresh(record)
        logger.info(f"✅ Saved manual entry to DB: ID={record.id}")
        
    except Exception as e:
        logger.error(f"❌ DATABASE ERROR: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        
    return {
        "message": "Klasifikasi manual berhasil disimpan",
        "extracted": {
            "distances": [payload.distance_1, payload.distance_2, payload.distance_3, payload.distance_4],
            "losses": [payload.loss_1, payload.loss_2, payload.loss_3, payload.loss_4],
            "total_ls": [payload.total_l_1, payload.total_l_2, payload.total_l_3, payload.total_l_4],
            "avg_ls": [payload.avg_l_1, payload.avg_l_2, payload.avg_l_3, payload.avg_l_4],
            "returns": [payload.return_1, payload.return_2, payload.return_3, payload.return_4],
        },
        "per_km": {
            "km1": {"distance": payload.distance_1, "loss": payload.loss_1, "total_l": payload.total_l_1, "avg_l": payload.avg_l_1, "return": payload.return_1},
            "km2": {"distance": payload.distance_2, "loss": payload.loss_2, "total_l": payload.total_l_2, "avg_l": payload.avg_l_2, "return": payload.return_2},
            "km3": {"distance": payload.distance_3, "loss": payload.loss_3, "total_l": payload.total_l_3, "avg_l": payload.avg_l_3, "return": payload.return_3},
            "km4": {"distance": payload.distance_4, "loss": payload.loss_4, "total_l": payload.total_l_4, "avg_l": payload.avg_l_4, "return": payload.return_4},
        },
        "prx": payload.prx,
        "prediction": pred.get("prediction"),
        "confidence": pred.get("confidence"),
        "status": pred.get("status"),
        "id": record.id,
    }