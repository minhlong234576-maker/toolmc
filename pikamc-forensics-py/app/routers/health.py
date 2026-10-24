"""GET /health - trạng thái hệ thống."""
import logging
import time

from fastapi import APIRouter

from app.config import get_settings
from app.database import db_size_kb
from app.services.pika_service import pika_service

logger = logging.getLogger("pikamc.health")
router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
async def health() -> dict[str, object]:
    """Trả về uptime, version, kích thước SQLite."""
    uptime = time.time() - settings.START_TIME if settings.START_TIME else 0.0
    return {
        "status": "ok",
        "uptime": round(uptime, 1),
        "version": settings.VERSION,
        "db_size_kb": db_size_kb(),
    }


