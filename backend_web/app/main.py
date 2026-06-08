# main.py - VERSION FIXED
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
from app.schemas import UserRegister, UserLogin, TokenResponse, UserOut
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
SHEET_ID  = "1dN2Q7zrp_M2RZ8o0-GPjYyL4yfZPo0KHjAKhEx8Qudo"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@optim.com")

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
    
    yield
    logger.info("Shutting down...")

app = FastAPI(title="OptiM API", version="2.0.1", lifespan=lifespan)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_FOLDER", "uploads"))
UPLOAD_DIR.mkdir(exist_ok=True)

# Global EasyOCR reader
easyocr_reader = None
easyocr_loading = False


# ═══════════════════════════════════════════════════════════════════
# SIMPLE & ROBUST OTDR PARSER
# ═══════════════════════════════════════════════════════════════════

def clean_ocr_decimal(val: float | None, max_expected: float) -> float | None:
    """Mengoreksi kesalahan OCR drop decimal point (misal 151 jadi 1.51)"""
    if val is None:
        return None
    sign = 1.0 if val >= 0 else -1.0
    val_abs = abs(float(val))
    if val_abs > max_expected:
        while val_abs > max_expected:
            val_abs /= 10.0
    return sign * val_abs


def parse_otdr_table_simple(raw_text: str) -> tuple[list, float]:
    """
    Parse teks OCR OTDR secara global dan dinamis (slicing token berdasarkan
    posisi anchor distance). Sangat kuat menghadapi ketiadaan newline (EasyOCR)
    maupun format tabel OCR.space yang rapi.
    """
    text = raw_text.replace(',', '.')
    
    # 1. Tokenisasi teks menjadi token numerik dan penanda dash ('---')
    raw_tokens = []
    for t in text.replace('\t', ' ').split():
        # Lewati token yang berisi huruf alfabet murni (jl, type, dll)
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

    logger.info(f"Raw numeric/dash tokens parsed: {raw_tokens}")

    # 2. Cari posisi indeks anchor distance (untuk KM1 ≈ 1.0, KM2 ≈ 2.0, dst.)
    anchors = {}
    last_idx = -1
    for i in range(1, 5):
        best_idx = -1
        # Cari token float pertama setelah anchor sebelumnya yang berada di jangkauan [i-0.25, i+0.25]
        for idx in range(last_idx + 1, len(raw_tokens)):
            val = raw_tokens[idx]
            if isinstance(val, float) and (i - 0.25 <= val <= i + 0.25):
                best_idx = idx
                break
        if best_idx != -1:
            anchors[i] = best_idx
            last_idx = best_idx
            
    # Taksir posisi anchor yang hilang (bila ada distance yang tidak terbaca sama sekali)
    for i in range(1, 5):
        if i not in anchors:
            if i - 1 in anchors:
                anchors[i] = min(anchors[i-1] + 6, len(raw_tokens) - 1)
            elif i + 1 in anchors:
                anchors[i] = max(anchors[i+1] - 6, 0)
            else:
                anchors[i] = min((i - 1) * 6, len(raw_tokens) - 1)
            
    sorted_anchors = sorted(anchors.items())
    logger.info(f"Distance anchors mapped: {anchors}")
    
    # 3. Potong token list berdasarkan anchor (Slicing)
    slices = {}
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
            
        # Potong token label baris di bagian belakang jika ada
        if len(row_tokens) > 3:
            last_token = row_tokens[-1]
            if isinstance(last_token, float):
                if last_token in (40.0, 41.0, 42.0, 43.0, 44.0) or (i < 4 and abs(last_token - (40 + i + 1)) < 0.1):
                    row_tokens.pop()
                    
        # Token pertama adalah distance
        dist = row_tokens[0]
        
        # Ekstrak return loss (nilai antara 30-65 atau 3000-6500)
        ret = -45.0
        ret_idx = -1
        for idx, val in enumerate(row_tokens):
            if isinstance(val, float) and (30.0 <= abs(val) <= 65.0 or 3000.0 <= abs(val) <= 6500.0):
                ret = -abs(val)
                ret_idx = idx
                break
        if ret_idx != -1:
            row_tokens.pop(ret_idx)
            
        ret = clean_ocr_decimal(ret, 65.0)
            
        # Ekstrak section (nilai sekitar 1.0, jika ada)
        sect = 1.0
        sect_idx = -1
        for idx, val in enumerate(row_tokens[1:], start=1):
            if isinstance(val, float) and 0.8 <= val <= 1.2:
                sect = val
                sect_idx = idx
                break
        if sect_idx != -1:
            row_tokens.pop(sect_idx)
        else:
            sect = dist if i == 1 else 1.0
            
        # Hapus token distance dari remaining list
        row_tokens.pop(0)
        
        # Sisa token dipetakan ke loss, total_l, avg_l
        loss = 0.0
        total_l = 0.0
        avg_l = 0.0
        
        remaining = [v for v in row_tokens if isinstance(v, float) or v == '---']
        
        if i == 4:
            loss = None
            pos_vals = [v for v in remaining if isinstance(v, float) and v > 0]
            pos_vals = [clean_ocr_decimal(v, 10.0) for v in pos_vals]
            if len(pos_vals) >= 2:
                total_l = pos_vals[0]
                avg_l = pos_vals[1]
            elif len(pos_vals) == 1:
                total_l = pos_vals[0]
        else:
            if len(remaining) >= 3:
                loss = clean_ocr_decimal(remaining[0], 3.0)
                total_l = clean_ocr_decimal(remaining[1], 10.0)
                avg_l = clean_ocr_decimal(remaining[2], 2.0)
            elif len(remaining) == 2:
                loss = None if '---' in remaining or remaining[0] == '---' else 0.0
                total_l = clean_ocr_decimal(remaining[0] if remaining[0] != '---' else remaining[1], 10.0)
                avg_l = clean_ocr_decimal(remaining[1] if remaining[0] != '---' else 0.0, 2.0)
            elif len(remaining) == 1:
                total_l = clean_ocr_decimal(remaining[0] if remaining[0] != '---' else 0.0, 10.0)
                
        row_data = {
            'distance': round(dist, 5),
            'section': round(sect, 5),
            'loss': round(loss, 3) if loss is not None else None,
            'total_l': round(total_l, 3),
            'avg_l': round(avg_l, 3),
            'return': round(ret, 2)
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
            
    # 7. Bulatkan hasil akhir
    for r in rows:
        r['distance'] = round(float(r['distance']), 5)
        r['section'] = round(float(r['section']), 5)
        r['loss'] = round(float(r['loss']), 3) if r['loss'] is not None else None
        r['total_l'] = round(float(r['total_l']), 3)
        r['avg_l'] = round(float(r['avg_l']), 3)
        r['return'] = round(float(r['return']), 2)

    logger.info(f"Final parsed rows: {rows}")
    return rows, avg_total



# ═══════════════════════════════════════════════════════════════════
# SIMPLE OCR PREPROCESSING (TIDAK AGGRESIF)
# ═══════════════════════════════════════════════════════════════════

def preprocess_image_simple(image_bytes: bytes) -> list:
    """Preprocessing sederhana untuk OCR - tidak overkill"""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    
    results = []
    
    # Crop area tabel — mulai dari 30% agar header tidak terpotong,
    # dan akhir sampai 99% agar baris terakhir (KM4) tidak terpotong
    y_start = int(h * 0.30)
    y_end = int(h * 0.99)
    cropped = img[y_start:y_end, 0:w]
    
    # Resize 2x untuk visibility
    resized = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    
    # Grayscale
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    
    # CLAHE moderate
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    
    # Binary threshold Otsu
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    results.append(Image.fromarray(binary))
    results.append(Image.fromarray(cv2.bitwise_not(binary)))
    
    return results


def easyocr_extract_simple(image_bytes: bytes) -> str:
    """Ekstrak teks menggunakan EasyOCR dengan timeout"""
    global easyocr_reader
    if easyocr_reader is None:
        return ""
    
    try:
        arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        
        # Crop sederhana — perluas ke 99% agar baris KM4 tidak terpotong
        y_start = int(h * 0.30)
        y_end = int(h * 0.99)
        cropped = img[y_start:y_end, 0:w]
        
        # Resize
        resized = cv2.resize(cropped, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        
        # EasyOCR read
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
    """Konversi markdown ke HTML"""
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
    context_state = request.get("context_state", None)
    
    if not user_message:
        return {"response": "Pesan tidak boleh kosong.", "source": "error"}
    
    if not GEMINI_API_KEY:
        return {
            "response": "Maaf, chatbot belum tersedia. API Key tidak ditemukan.",
            "source": "error"
        }
    
    try:
        # Ambil data dari database
        result_total = await db.execute(select(func.count(OtdrResult.id)))
        total_data = result_total.scalar() or 0
        
        result_latest = await db.execute(
            select(OtdrResult).order_by(OtdrResult.timestamp.desc()).limit(1)
        )
        latest = result_latest.scalar_one_or_none()
        
        prompt = f"""Anda adalah asisten AI untuk aplikasi OptiM (Intelligent Fiber Monitoring).

[DATA REAL-TIME DARI DATABASE]
- Total data pengukuran: {total_data}
- Klasifikasi terakhir: {latest.klasifikasi if latest else 'Belum ada'}
- Status terakhir: {latest.status if latest else 'Belum ada'}
- Loss terakhir: {latest.loss_1 if latest else 0} dB, {latest.loss_2 if latest else 0} dB, {latest.loss_3 if latest else 0} dB, {latest.loss_4 if latest else 0} dB
- Prx: {latest.prx if latest else 0} dBm

[PERTANYAAN PENGGUNA]
{user_message}

[JAWABAN]
Jawab dengan bahasa Indonesia yang ramah dan profesional. Format output dengan HTML dasar (<strong>, <br>, <ul>, <li>).
"""
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30)
            data = response.json()
            
            if response.status_code == 200:
                reply = data["candidates"][0]["content"]["parts"][0]["text"]
                reply_html = format_markdown_to_html(reply)
                return {"response": reply_html, "source": "gemini_api"}
            else:
                error_msg = data.get("error", {}).get("message", str(data))
                return {
                    "response": f"Maaf, terjadi kesalahan: {error_msg[:200]}",
                    "source": "error"
                }
    except Exception as e:
        logger.error(f"Chat exception: {e}")
        return {
            "response": f"Maaf, terjadi kesalahan: {str(e)[:200]}",
            "source": "error"
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
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


@app.get("/api/admin/users/all")
async def get_all_users(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "is_approved": u.is_approved,
                "is_admin": u.is_admin,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    }


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
# DETECTION OCR - MAIN ENDPOINT (FIXED)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/detect")
async def detect_ocr(
    file: UploadFile = File(...),
    prx_manual: float = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_optional_user),
):
    # Validate file
    allowed = {"image/jpeg", "image/png", "image/jpg", "image/bmp", "image/tiff"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Format gambar tidak didukung.")
    
    content = await file.read()
    raw_text = ""
    ocr_method = "none"
    
    logger.info("=" * 70)
    logger.info("🔄 Starting OCR process...")
    
    # Try OCR methods
    async def run_easyocr():
        if easyocr_reader is not None:
            return easyocr_extract_simple(content)
        return ""
    
    async def run_tesseract():
        return tesseract_extract(content)
    
    async def run_ocrspace():
        return ocr_space_extract(content)
    
    try:
        # Run OCR methods concurrently
        easyocr_task = asyncio.create_task(run_easyocr())
        tesseract_task = asyncio.create_task(run_tesseract())
        ocrspace_task = asyncio.create_task(run_ocrspace())
        
        # Wait for all with timeout
        done, pending = await asyncio.wait(
            [easyocr_task, tesseract_task, ocrspace_task],
            timeout=20.0,
            return_when=asyncio.ALL_COMPLETED
        )
        
        # Check results
        best_text = ""
        best_score = 0
        
        for task in done:
            try:
                text = task.result()
                if text:
                    score = len(re.findall(r'\d+\.\d{3,}', text))
                    if score > best_score:
                        best_score = score
                        best_text = text
                        if task == easyocr_task:
                            ocr_method = "easyocr"
                        elif task == tesseract_task:
                            ocr_method = "tesseract"
                        else:
                            ocr_method = "ocr.space"
            except Exception as e:
                logger.error(f"OCR task error: {e}")
        
        raw_text = best_text
        logger.info(f"✅ Best OCR: {ocr_method} with {best_score} decimal numbers")
        
    except Exception as e:
        logger.error(f"OCR error: {e}")
    
    if not raw_text or len(raw_text.strip()) < 20:
        raise HTTPException(
            status_code=400,
            detail="Gambar tidak dapat dibaca. Pastikan foto jelas dan tabel OTDR terlihat."
        )
    
    logger.info(f"📝 RAW TEXT ({ocr_method}):\n{raw_text[:500]}")
    
    # Parse using simple parser
    rows, avg_total = parse_otdr_table_simple(raw_text)
    
    # Extract Prx
    prx_from_ocr = extract_prx(raw_text)
    final_prx = prx_manual if prx_manual is not None else (prx_from_ocr if prx_from_ocr else -25.0)
    
    logger.info(f"📊 Parsed rows: {len(rows)}")
    for i, row in enumerate(rows):
        logger.info(f"   KM{i+1}: dist={row['distance']} loss={row['loss']} total_l={row['total_l']}")
    
    # Validate
    valid = [r for r in rows if r['distance'] > 0.5]
    if len(valid) < 2:
        raise HTTPException(
            status_code=400,
            detail=f"Hanya {len(valid)} baris valid terdeteksi (butuh minimal 2)."
        )
    
    # ML Prediction - WITH DETAILED ERROR HANDLING
    logger.info("=" * 50)
    logger.info("Starting ML Prediction...")
    
    otdr_values = {
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
    
    logger.info(f"📊 OTDR Values for ML: {otdr_values}")
    
    try:
        pred = await asyncio.to_thread(ml.predict_from_otdr, otdr_values)
        logger.info(f"🤖 ML prediction SUCCESS: {pred.get('prediction')} (confidence: {pred.get('confidence')}%)")
    except Exception as e:
        logger.error(f"❌ ML prediction FAILED: {e}")
        import traceback
        traceback.print_exc()
        pred = {
            "prediction": "Normal",
            "confidence": 70.0,
            "status": "Normal"
        }
        logger.info(f"📌 Using fallback prediction: {pred}")
    
    # Save to database - WITH DETAILED ERROR HANDLING
    logger.info("=" * 50)
    logger.info("💾 Saving to Database...")
    user_id = current_user.id if current_user else 1
    logger.info(f"👤 User ID: {user_id}")
    
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
        
        logger.info("✅ OtdrResult object created successfully")
        db.add(record)
        logger.info("✅ Record added to session")
        
        await db.commit()
        logger.info("✅ Database commit successful")
        
        await db.refresh(record)
        logger.info(f"✅ Record refreshed, ID={record.id}")
        
    except Exception as e:
        logger.error(f"❌ DATABASE ERROR: {e}")
        import traceback
        traceback.print_exc()
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    logger.info(f"✅ Successfully saved to DB: ID={record.id}")
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
        "per_km": {
            "km1": rows[0],
            "km2": rows[1],
            "km3": rows[2],
            "km4": rows[3],
        },
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
        .where(OtdrResult.user_id == current_user.id)
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
            "return_1": r.return_1, "return_2": r.return_2,
            "return_3": r.return_3, "return_4": r.return_4,
            "distance_1": r.distance_1, "distance_2": r.distance_2,
            "distance_3": r.distance_3, "distance_4": r.distance_4,
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
            .where(OtdrResult.user_id == current_user.id)
            .order_by(OtdrResult.timestamp.desc())
            .offset(skip)
            .limit(limit)
        )
    else:
        result = await db.execute(
            select(OtdrResult)
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
        .where(OtdrResult.user_id == current_user.id)
        .order_by(OtdrResult.timestamp.asc())
        .offset(index)
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if not record:
        return await get_slide_data(0, db, current_user)
    
    total_result = await db.execute(
        select(func.count(OtdrResult.id)).where(OtdrResult.user_id == current_user.id)
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
    
    # Delete existing sheets data for this user
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
            
            timestamp = datetime.now()
            time_col = next((c for c in df.columns if 'time' in c.lower()), None)
            if time_col and pd.notna(row.get(time_col)):
                try:
                    timestamp = pd.to_datetime(row[time_col])
                except Exception:
                    pass
            
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
            
            pred = ml.predict_from_otdr(otdr_values)
            
            record = OtdrResult(
                user_id=current_user.id,
                timestamp=timestamp,
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