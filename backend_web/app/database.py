from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

# PostgreSQL connection string
# Format: postgresql+asyncpg://username:password@host:port/database
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://optim_user:optim2026@localhost:5432/optim_db"
)

# ============================================================
# CONNECTION POOL YANG STABIL UNTUK PRODUCTION
# ============================================================
engine = create_async_engine(
    DATABASE_URL,
    echo=False,                    # Matikan echo di production (bikin lambat)
    pool_size=10,                  # Jumlah koneksi tetap di pool
    max_overflow=20,               # Koneksi tambahan jika pool penuh
    pool_timeout=30,               # Timeout menunggu koneksi (detik)
    pool_recycle=1800,             # Refresh koneksi setiap 30 menit
    pool_pre_ping=True,            # ⭐ CEK KONEKSI SEBELUM DIPAKAI
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()