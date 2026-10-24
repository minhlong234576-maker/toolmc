"""FastAPI app entry point: mount static, register routers, init_db on startup."""
import logging
import time

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import get_settings
from app.database import init_db
from app.routers import ai, health, history, investigate, pika, scan
from app.routers import settings as settings_router
from app.security.rate_limit import limiter
from app.services.pika_service import pika_service

settings = get_settings()

logging.basicConfig(level=settings.LOG_LEVEL.upper(),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pikamc.main")

app = FastAPI(
    title="PikaMC Forensics Investigator",
    description="Phân tích gian lận Minecraft Server - READ-ONLY TUYỆT ĐỐI",
    version=settings.VERSION,
    lifespan=lambda a: _lifespan(a),
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Lifespan: set START_TIME + init SQLite WAL + nạp settings đã lưu + đóng client lúc shutdown."""
    settings.START_TIME = time.time()
    init_db()

    # Nạp cấu hình runtime đã lưu từ SQLite (nếu có)
    from app.database import SessionLocal
    from app.services.settings_service import apply_runtime_settings, get_all_settings

    db = SessionLocal()
    try:
        saved_db_settings = get_all_settings(db)
        if saved_db_settings:
            apply_runtime_settings(saved_db_settings)
            logger.info("Đã đồng bộ %d cài đặt từ SQLite vào runtime", len(saved_db_settings))
    except Exception as e:
        logger.warning("Không thể nạp cài đặt từ SQLite lúc khởi động: %s", e)
    finally:
        db.close()

    logger.info("PikaMC Forensics v%s iniciado — READ-ONLY mode", settings.VERSION)
    yield
    await pika_service.close()

# CORS cho dev frontend ngoài.
# Lưu ý: allow_origins=["*"] không hợp lệ khi allow_credentials=True (trình duyệt
# sẽ từ chối response). App xác thực bằng header X-API-Key, không dùng cookie,
# nên tắt credentials để CORS hoạt động đúng với wildcard origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)

# Rate limiting state
app.state.limiter = limiter


# Routers
app.include_router(health.router)
app.include_router(pika.router)
app.include_router(investigate.router)
app.include_router(scan.router)
app.include_router(ai.router)
app.include_router(history.router)
app.include_router(settings_router.router)

# Frontend single-file
import sys
from pathlib import Path


def get_static_index() -> Path:
    """Xác định đường dẫn index.html hỗ trợ cả môi trường dev và PyInstaller (.exe)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "app" / "static" / "index.html"
        if bundled.exists():
            return bundled
    return Path(__file__).resolve().parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve index.html."""
    return FileResponse(get_static_index(), media_type="text/html")

