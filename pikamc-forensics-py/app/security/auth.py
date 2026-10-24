"""Auth dependency: xác thực API key từ header X-API-Key (optional nếu chưa set)."""
from typing import Any

from fastapi import Header, HTTPException, Query, status

from app.config import get_settings

settings = get_settings()

# Đặt false khi làm dev/localhost không cần auth
FRONTEND_OPEN_ACCESS = True


def require_api_key(
    x_api_key: str | None = Header(default=None, description="API key"),
    api_key_query: str | None = Query(default=None, alias="api_key"),
) -> dict[str, Any]:
    """Kiểm tra API key cho các route cần bảo vệ.

    Khi FRONTEND_OPEN_ACCESS=True chỉ warn nếu sai key (dùng cho local dev).
    """
    if settings.API_SECRET and settings.API_SECRET != "your_frontend_auth_secret":
        key = x_api_key or api_key_query
        if key != settings.API_SECRET:
            if FRONTEND_OPEN_ACCESS:
                # Dev mode: log warn, không chặn
                return {"auth": "invalid-key-ignored (dev mode)"}
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key không hợp lệ")
    return {"auth": "ok"}
