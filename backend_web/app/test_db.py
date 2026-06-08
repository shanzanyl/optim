# test_db.py
import asyncio
from app.database import AsyncSessionLocal, engine
from sqlalchemy import text

async def test():
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            print("✅ Database connection OK")
    except Exception as e:
        print(f"❌ Database error: {e}")
    finally:
        await engine.dispose()

asyncio.run(test())