from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


settings = get_settings()
DATABASE_URL = normalize_database_url(settings.database_url)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_async_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=not DATABASE_URL.startswith("sqlite"),
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def ensure_sqlite_directory() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return

    prefix = "sqlite+aiosqlite:///"
    if not DATABASE_URL.startswith(prefix):
        return

    db_path = DATABASE_URL.removeprefix(prefix)
    if db_path in {":memory:", ""}:
        return

    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


async def init_db() -> None:
    ensure_sqlite_directory()
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

