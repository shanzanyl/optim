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

engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

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