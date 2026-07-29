# backend_web/app/local_folder_watcher.py
# Auto-proses file SOR dari folder LOKAL di dalam project (bukan Google Drive) —
# folder ini ikut ter-deploy bersama kode lewat git push.
# Tiap N menit, sistem cek folder, proses SEMUA file yang ada lewat pipeline
# yang SUDAH ADA (process_sor_core di main.py) — tanpa mengubah logika pipeline.
#
# Tidak ada tracking/marking file "sudah diproses" (sesuai permintaan):
# SETIAP putaran akan memproses ULANG semua file yang ada di folder.
# Kalau folder kosong, putaran itu di-skip tanpa error.

import logging
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Folder tempat file CSV/Excel disimpan — ikut ter-commit ke git, ter-deploy otomatis.
# Bisa di-override lewat env var kalau perlu, default ke folder di dalam project.
INCOMING_FOLDER = Path(os.getenv("LOCAL_INCOMING_FOLDER", str(BASE_DIR / "data_incoming")))

POLL_INTERVAL_MIN = int(os.getenv("LOCAL_POLL_INTERVAL_MIN", "15"))

ALLOWED_EXTENSIONS = ('.xlsx', '.xls', '.csv')


async def check_local_folder():
    """
    Dipanggil otomatis tiap POLL_INTERVAL_MIN menit oleh scheduler.
    Baca semua file di INCOMING_FOLDER, proses lewat pipeline yang sudah ada,
    TANPA cek status "sudah diproses" — semua file diproses ulang tiap putaran.
    """
    from app.main import process_sor_core

    logger.info("[LOCAL_WATCHER] ── Mulai pengecekan folder lokal ──")
    logger.info(f"[LOCAL_WATCHER]   folder = {INCOMING_FOLDER}")

    if not INCOMING_FOLDER.exists():
        logger.warning(f"[LOCAL_WATCHER] Folder {INCOMING_FOLDER} tidak ditemukan, skip putaran ini")
        return

    files = [
        f for f in INCOMING_FOLDER.iterdir()
        if f.is_file() and f.name.lower().endswith(ALLOWED_EXTENSIONS)
    ]

    if not files:
        logger.info("[LOCAL_WATCHER] Folder kosong, tidak ada file. Menunggu putaran berikutnya.")
        return

    logger.info(f"[LOCAL_WATCHER] Ditemukan {len(files)} file di folder")

    for f in files:
        try:
            content = f.read_bytes()
            logger.info(f"[LOCAL_WATCHER] ✅ Baca '{f.name}' ({len(content)} bytes)")

            # Panggil pipeline yang SAMA PERSIS dipakai upload manual —
            # tidak ada logika baru di sini, cuma sumber file-nya beda.
            async with AsyncSessionLocal() as db:
                result = await process_sor_core(
                    content=content,
                    filename=f.name,
                    user_id=None,          # otomatis, tidak terikat user manapun
                    db=db,
                )
                logger.info(
                    f"[LOCAL_WATCHER] ✅ '{f.name}' selesai diproses → "
                    f"classification={result['classification']}, status={result['status']}"
                )

        except Exception as e:
            # Satu file gagal tidak boleh menghentikan file lain di putaran yang sama
            logger.error(f"[LOCAL_WATCHER] ❌ Gagal memproses '{f.name}': {e}", exc_info=True)
            continue

    logger.info("[LOCAL_WATCHER] ── Selesai satu putaran pengecekan ──")


def start_local_scheduler():
    """Dipanggil sekali dari lifespan main.py saat startup aplikasi."""
    from datetime import datetime, timedelta

    scheduler = AsyncIOScheduler()
    # PENTING: next_run_time=None membuat job TIDAK PERNAH jalan sama sekali
    # (sudah diverifikasi lewat tes langsung) — beda dari asumsi awal "nunggu
    # interval pertama". Yang benar: hitung eksplisit waktu run pertama.
    first_run = datetime.now() + timedelta(minutes=POLL_INTERVAL_MIN)
    scheduler.add_job(
        check_local_folder,
        "interval",
        minutes=POLL_INTERVAL_MIN,
        id="local_folder_watcher_job",
        next_run_time=first_run,
    )
    scheduler.start()
    logger.info(f"[LOCAL_WATCHER] ✅ Scheduler aktif — cek pertama pada {first_run}, lalu tiap {POLL_INTERVAL_MIN} menit")
    return scheduler