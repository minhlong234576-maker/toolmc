"""Read + Write settings đã lưu trong SQLite + Đồng bộ Runtime & .env.

Cho phép người dùng thiết lập PIKAMC_API_KEY, PIKAMC_API_URL cũng như tất cả AI providers và models trực tiếp từ giao diện web.
"""
import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.settings_service import (
    apply_runtime_settings,
    get_all_settings,
    get_setting,
    mask_key,
    set_setting,
)

logger = logging.getLogger("pikamc.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    key: str = Field(..., min_length=1)
    value: object  # nhận bất kỳ type JSON


class ReadOnlyToggleRequest(BaseModel):
    read_only: bool


class SystemConfigUpdate(BaseModel):
    pikamc_api_key: str | None = None
    pikamc_api_url: str | None = None
    xai247_api_key: str | None = None
    xai247_api_url: str | None = None
    xai247_model: str | None = None
    meta_api_key: str | None = None
    meta_api_url: str | None = None
    meta_model: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    ollama_host: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    strict_read_only: bool | None = None


class TestPikaRequest(BaseModel):
    api_key: str | None = None
    api_url: str | None = None


class TestAIRequest(BaseModel):
    provider: Literal["xai247", "meta", "gemini", "openai", "ollama"]
    api_key: str | None = None
    api_url: str | None = None
    model: str | None = None


@router.get("")
async def read_all(db: Session = Depends(get_db)) -> dict[str, object]:
    """Đọc toàn bộ settings từ SQLite."""
    return get_all_settings(db)


@router.get("/config")
async def get_system_config(
    reveal: bool = Query(False, description="Hiện đầy đủ key thay vì che bớt"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Đọc cấu hình hệ thống hiện tại (PikaMC + tất cả các model/provider AI)."""
    settings = get_settings()

    def _val(actual: str | None, has_val: bool) -> str:
        if not has_val or not actual:
            return ""
        return actual if reveal else mask_key(actual)

    db_settings = get_all_settings(db)
    active_ro = getattr(settings, "READ_ONLY_MODE", True)

    return {
        "pikamc": {
            "api_url": settings.PIKAMC_API_URL,
            "api_key": _val(settings.PIKAMC_API_KEY, bool(settings.PIKAMC_API_KEY)),
            "has_api_key": bool(settings.PIKAMC_API_KEY),
        },
        "read_only_mode": active_ro,
        "ai": {
            "default_provider": db_settings.get("default_provider", "xai247"),
            "default_model": db_settings.get("default_model", ""),
            "strict_read_only": active_ro,
            "xai247": {
                "api_url": settings.XAI247_API_URL,
                "api_key": _val(settings.XAI247_API_KEY, bool(settings.XAI247_API_KEY)),
                "model": settings.XAI247_MODEL,
                "has_api_key": bool(settings.XAI247_API_KEY),
            },
            "meta": {
                "api_url": settings.META_API_URL,
                "api_key": _val(settings.META_API_KEY, bool(settings.META_API_KEY)),
                "model": settings.META_MODEL,
                "has_api_key": bool(settings.META_API_KEY),
            },
            "gemini": {
                "api_key": _val(settings.GEMINI_API_KEY, bool(settings.GEMINI_API_KEY)),
                "model": "gemini-1.5-flash",
                "has_api_key": bool(settings.GEMINI_API_KEY),
            },
            "openai": {
                "api_key": _val(settings.OPENAI_API_KEY, bool(settings.OPENAI_API_KEY)),
                "model": "gpt-4o-mini",
                "has_api_key": bool(settings.OPENAI_API_KEY),
            },
            "ollama": {
                "host": settings.OLLAMA_HOST,
                "model": "qwen2.5:0.5b",
                "configured": True,
            },
        },
    }


@router.post("/config")
async def update_system_config(
    req: SystemConfigUpdate,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Cập nhật hàng loạt cấu hình (PikaMC API Key, URL, AI Providers/Models).

    Tự động áp dụng ngay vào runtime mà không cần khởi động lại app và ghi lưu vào .env lẫn SQLite.
    """
    settings = get_settings()
    updates: dict[str, str] = {}

    def is_valid_new_key(val: str | None) -> bool:
        if not val or not val.strip():
            return False
        if "••" in val or "..." in val:
            return False
        return True

    # PikaMC
    if req.pikamc_api_url and req.pikamc_api_url.strip():
        updates["pikamc_api_url"] = req.pikamc_api_url.strip()
    if is_valid_new_key(req.pikamc_api_key):
        updates["pikamc_api_key"] = req.pikamc_api_key.strip()

    # XAI247
    if req.xai247_api_url and req.xai247_api_url.strip():
        u = req.xai247_api_url.strip().rstrip("/")
        if "/dashboard" in u:
            u = "https://xai247.xyz/v1"
        elif not u.endswith("/v1"):
            u = f"{u}/v1"
        updates["xai247_api_url"] = u
    if is_valid_new_key(req.xai247_api_key):
        updates["xai247_api_key"] = req.xai247_api_key.strip()
    if req.xai247_model and req.xai247_model.strip():
        updates["xai247_model"] = req.xai247_model.strip()

    # Meta AI
    if req.meta_api_url and req.meta_api_url.strip():
        updates["meta_api_url"] = req.meta_api_url.strip()
    if is_valid_new_key(req.meta_api_key):
        updates["meta_api_key"] = req.meta_api_key.strip()
    if req.meta_model and req.meta_model.strip():
        updates["meta_model"] = req.meta_model.strip()

    # Gemini & OpenAI
    if is_valid_new_key(req.gemini_api_key):
        updates["gemini_api_key"] = req.gemini_api_key.strip()
    if is_valid_new_key(req.openai_api_key):
        updates["openai_api_key"] = req.openai_api_key.strip()

    # Ollama
    if req.ollama_host and req.ollama_host.strip():
        updates["ollama_host"] = req.ollama_host.strip()

    # Read-Only Mode
    if req.strict_read_only is not None:
        updates["strict_read_only"] = req.strict_read_only

    # Áp dụng runtime vào Settings in-memory và file .env
    if updates:
        apply_runtime_settings(updates)

    # Lưu thêm vào SQLite
    if req.default_provider is not None:
        set_setting(db, "default_provider", req.default_provider)
    if req.default_model is not None:
        set_setting(db, "default_model", req.default_model)
    if req.strict_read_only is not None:
        set_setting(db, "strict_read_only", req.strict_read_only)

    for k, v in updates.items():
        set_setting(db, k, v)

    logger.info("Đã cập nhật cấu hình hệ thống: %s", list(updates.keys()))
    return {
        "ok": True,
        "updated_keys": list(updates.keys()),
        "message": "Đã lưu cấu hình thành công! Dữ liệu đã được áp dụng trực tiếp cho hệ thống.",
    }


@router.post("/read-only")
async def toggle_read_only(
    req: ReadOnlyToggleRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Bật/tắt nhanh chế độ Read-Only trực tiếp từ Header hoặc UI."""
    apply_runtime_settings({"strict_read_only": req.read_only})
    set_setting(db, "strict_read_only", req.read_only)
    msg = (
        "Đã BẬT chế độ Chỉ Đọc (Read-Only) an toàn. AI chỉ phân tích và bị cấm đề xuất lệnh ban/kick/xóa."
        if req.read_only
        else "Đã TẮT chế độ Chỉ Đọc. Kích hoạt toàn quyền Quản trị viên (được phép đề xuất lệnh console /ban, /kick, /op)."
    )
    return {
        "ok": True,
        "read_only": req.read_only,
        "message": msg,
    }


@router.post("/test-pika")
async def test_pika_connection(
    req: TestPikaRequest | None = None,
) -> dict[str, object]:
    """Kiểm tra kết nối trực tiếp tới PikaMC API."""
    settings = get_settings()
    api_key = (req.api_key.strip() if req and req.api_key and "••" not in req.api_key else settings.PIKAMC_API_KEY) or ""
    api_url = (req.api_url.strip() if req and req.api_url else settings.PIKAMC_API_URL) or "https://hub.pikamc.vn"

    if api_url.endswith("/api"):
        api_url = api_url[:-4]
    api_url = api_url.rstrip("/")

    if not api_key:
        return {"ok": False, "error": "Chưa có PikaMC API Key! Vui lòng nhập key."}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{api_url}/api/client/servers",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Accept": "application/json, text/plain, */*",
                },
            )
            duration_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                servers = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                return {
                    "ok": True,
                    "status_code": 200,
                    "latency_ms": duration_ms,
                    "server_count": len(servers),
                    "message": f"Kết nối PikaMC API thành công ({duration_ms}ms)! Tìm thấy {len(servers)} máy chủ.",
                }
            if resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "status_code": resp.status_code,
                    "latency_ms": duration_ms,
                    "error": "API Key không hợp lệ hoặc không có quyền truy cập API PikaMC!",
                }
            return {
                "ok": False,
                "status_code": resp.status_code,
                "latency_ms": duration_ms,
                "error": f"PikaMC API trả về lỗi HTTP {resp.status_code}: {resp.text[:150]}",
            }
    except Exception as e:
        return {
            "ok": False,
            "error": f"Không thể kết nối tới PikaMC ({api_url}): {e}",
        }


@router.post("/test-ai")
async def test_ai_connection(req: TestAIRequest) -> dict[str, object]:
    """Kiểm tra kết nối và khả năng suy luận của mô hình AI."""
    from app.services.ai_service import AIOptions, analyze_with_provider

    t0 = time.perf_counter()
    options = AIOptions(
        model=req.model or None,
        max_tokens=25,
        temperature=0.1,
        api_key=req.api_key or None,
        api_url=req.api_url or None,
    )

    try:
        response_text, duration_ms = analyze_with_provider(
            "Ping kiểm tra kết nối từ hệ thống Giám định Minecraft.",
            req.provider,
            options,
            "Trả lời ngắn gọn dưới 10 chữ rằng kết nối AI thành công.",
        )
        return {
            "ok": True,
            "provider": req.provider,
            "latency_ms": duration_ms or int((time.perf_counter() - t0) * 1000),
            "response": response_text.strip()[:200],
            "message": f"Kết nối AI {req.provider} hoạt động tốt ({duration_ms}ms)!",
        }
    except Exception as e:
        return {
            "ok": False,
            "provider": req.provider,
            "error": str(e),
        }


@router.get("/{key}")
async def read_one(key: str, db: Session = Depends(get_db)) -> dict[str, object]:
    """Đọc 1 setting (trả default nếu chưa có)."""
    value = get_setting(db, key)
    return {"key": key, "value": value}


@router.put("")
async def write_one(req: SettingUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    """Lưu 1 setting (upsert)."""
    ok = set_setting(db, req.key, req.value)
    if not ok:
        raise HTTPException(500, "Ghi setting thất bại")
    logger.info("Setting '%s' đã được ghi", req.key)
    return {"key": req.key, "value": req.value, "updated": True}

