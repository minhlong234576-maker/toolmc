"""Entry point tiện dụng: chạy `python run.py` thay vì nhớ lệnh uvicorn.

SV đọc cấu hình host/port từ .env (mặc định 0.0.0.0:8000).
"""
import uvicorn

from app.config import get_settings

settings = get_settings()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
    )
