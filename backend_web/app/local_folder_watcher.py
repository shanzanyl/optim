# backend_web/app/local_folder_watcher.py
# Proses file SOR dari folder lokal SATU PER SATU, dipicu oleh frontend
# (bukan lagi dijadwalkan APScheduler tiap interval tetap) — supaya file
# berikutnya baru diproses SETELAH trace file sebelumnya selesai diputar
# di Dashboard. Urutan file mengikuti urutan nama file (diurutkan), lalu
# mengulang dari awal lagi kalau sudah habis (boleh berulang).

import logging
from pathlib import Path

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

import os

BASE_DIR = Path(__file__).resolve().parent.parent
INCOMING_FOLDER = Path(os.getenv("LOCAL_INCOMING_FOLDER", str(BASE_DIR / "data_incoming")))
ALLOWED_EXTENSIONS = ('.xlsx', '.xls', '.csv')

# Posisi file berikutnya yang akan diproses — disimpan di memori (bukan
# database), jadi otomatis reset ke awal setiap kali server restart.
# Ini sengaja dibiarkan sederhana karena boleh berulang/tidak berurutan
# ketat sekalipun server sempat restart di tengah jalan.
_current_index = 0


def _list_files():
    if not INCOMING_FOLDER.exists():
        return []
    files = [
        f for f in INCOMING_FOLDER.iterdir()
        if f.is_file() and f.name.lower().endswith(ALLOWED_EXTENSIONS)
    ]
    return sorted(files, key=lambda f: f.name)


async def get_next_file_trace():
    """
    Ambil file BERIKUTNYA dalam urutan (mengulang dari awal kalau sudah habis),
    proses lewat pipeline yang sama seperti upload manual, simpan ke database,
    lalu kembalikan data trace-nya supaya bisa langsung ditampilkan + di-Play
    otomatis di frontend.

    Return None kalau folder kosong / tidak ada file yang valid.
    """
    global _current_index
    from app.main import process_sor_core

    files = _list_files()
    if not files:
        logger.info("[LOCAL_WATCHER] Folder kosong, tidak ada file untuk diproses.")
        return None

    _current_index = _current_index % len(files)
    target = files[_current_index]
    _current_index += 1  # siapkan untuk panggilan berikutnya

    logger.info(f"[LOCAL_WATCHER] ▶ Memproses file berikutnya: '{target.name}' (index {_current_index-1}/{len(files)})")

    try:
        content = target.read_bytes()
        async with AsyncSessionLocal() as db:
            result = await process_sor_core(
                content=content,
                filename=target.name,
                user_id=None,
                db=db,
            )
        logger.info(
            f"[LOCAL_WATCHER] ✅ '{target.name}' selesai diproses → "
            f"classification={result['classification']}, status={result['status']}"
        )
        return result
    except Exception as e:
        logger.error(f"[LOCAL_WATCHER] ❌ Gagal memproses '{target.name}': {e}", exc_info=True)
        raise