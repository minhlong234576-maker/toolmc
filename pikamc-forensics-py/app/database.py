"""SQLite connection + init. WAL mode + check_same_thread=False cho FastAPI."""
import logging
import os
import time
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

logger = logging.getLogger("pikamc.database")

settings = get_settings()

# Đảm bảo thư mục data tồn tại trước khi tạo engine
_db_path = settings.DATABASE_URL.replace("sqlite:///", "")
_db_dir = os.path.dirname(_db_path)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

engine: Engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},  # FastAPI đa thread
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class cho toàn bộ ORM models."""


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
    """Bật WAL mode + foreign keys cho mọi SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Tạo toàn bộ bảng khi app khởi động (idempotent)."""
    from app import models  # noqa: F401 — đăng ký models vào Base.metadata

    start = time.perf_counter()
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized in %.2fms (%s)", (time.perf_counter() - start) * 1000, settings.DATABASE_URL)


def get_db():
    """Dependency: session cho mỗi request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_size_kb() -> float:
    """Kích thước file SQLite (KB) - dùng cho /health."""
    if not os.path.exists(_db_path):
        return 0.0
    return round(os.path.getsize(_db_path) / 1024, 2)
