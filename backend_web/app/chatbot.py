# backend_web/app/chatbot.py
import os
import re
from google import genai
from dotenv import load_dotenv
# from backend_web import app
#from google.genai import types

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
# GEMINI AI CHATBOT - Direct API
# ═══════════════════════════════════════════════════════════════════

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def format_markdown_to_html(text: str) -> str:
    """Konversi markdown ke HTML"""
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    html = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html)
    html = html.replace('\n', '<br>')
    return html

# @app.post("/api/chat")
# async def chat(request: dict):
#     user_message = request.get("message", "").strip()
#     context_state = request.get("context_state", None)
    
#     if not user_message:
#         return {"response": "Pesan tidak boleh kosong.", "source": "error"}
    
#     if not GEMINI_API_KEY:
#         return {
#             "response": "Maaf, chatbot belum tersedia. API Key tidak ditemukan.",
#             "source": "error"
#         }
    
#     # Susun prompt dengan konteks
#     prompt = user_message
#     if context_state:
#         context_text = "\n".join([f"- {k}: {v}" for k, v in context_state.items()])
#         prompt = f"[KONTEKS DATA SAAT INI]\n{context_text}\n\nPertanyaan: {user_message}"
    
#     # Gunakan model gemini-2.5-flash (pasti tersedia dari hasil curl)
#     url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
#     payload = {
#         "contents": [{
#             "parts": [{"text": prompt}]
#         }],
#         "system_instruction": {
#             "parts": [{"text": (
#                 "Anda adalah asisten AI untuk aplikasi OptiM, platform monitoring jaringan fiber optik. "
#                 "Bantulah menjawab pertanyaan pengguna dengan jelas, ramah, dan solutif. "
#                 "Gunakan bahasa Indonesia yang baik. "
#                 "Format output Anda langsung menggunakan tag HTML dasar (seperti <strong>, <br>, <ul>, <li>) "
#                 "agar tampilannya rapi di chatbox web. "
#                 "Jika ditanya tentang data jaringan fiber optik, berikan penjelasan yang informatif."
#             )}]
#         }
#     }
    
#     try:
#         async with httpx.AsyncClient() as client:
#             response = await client.post(url, json=payload, timeout=30)
#             data = response.json()
            
#             if response.status_code == 200:
#                 reply = data["candidates"][0]["content"]["parts"][0]["text"]
#                 reply_html = format_markdown_to_html(reply)
#                 return {"response": reply_html, "source": "gemini_api"}
#             else:
#                 error_msg = data.get("error", {}).get("message", str(data))
#                 print(f"Gemini API error: {error_msg}")
                
#                 if "429" in error_msg:
#                     return {
#                         "response": "⚠️ **Layanan AI sedang sibuk.**\n\nSilakan coba lagi dalam beberapa menit. Terima kasih! 🙏",
#                         "source": "rate_limit"
#                     }
#                 else:
#                     return {
#                         "response": f"Maaf, terjadi kesalahan: {error_msg[:200]}",
#                         "source": "error"
#                     }
#     except Exception as e:
#         print(f"Gemini API exception: {e}")
#         return {
#             "response": f"Maaf, terjadi kesalahan: {str(e)[:200]}",
#             "source": "error"
#         }
