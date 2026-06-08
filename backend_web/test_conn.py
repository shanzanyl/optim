import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://optim_user:optim2026@localhost:5432/optim_db"

async def test():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT current_user, version()"))
            user, version = result.fetchone()
            print(f"✅ Berhasil terhubung!")
            print(f"   User: {user}")
            print(f"   PostgreSQL Version: {version[:50]}...")
    except Exception as e:
        print(f"❌ Gagal konek: {e}")
    finally:
        await engine.dispose()

asyncio.run(test())