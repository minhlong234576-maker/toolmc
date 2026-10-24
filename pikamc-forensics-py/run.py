"""Entry point cho PikaMC Forensics Investigator (hỗ trợ cả chạy trực tiếp và đóng gói .exe)."""
import os
import sys
import threading
import time
import webbrowser

# Hỗ trợ Windows console code-page (tránh lỗi UnicodeEncodeError charmap cp1252/cp437)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Nếu chạy dạng đóng gói .exe (frozen), đảm bảo thư mục làm việc là thư mục chứa file .exe
if getattr(sys, "frozen", False):
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(exe_dir)

import uvicorn
from app.config import get_settings
from app.main import app

settings = get_settings()


def open_browser(url: str, delay: float = 1.2) -> None:
    """Mở trình duyệt sau khi server khởi động."""
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def safe_print(msg: str) -> None:
    """In an toàn không bao giờ throw UnicodeEncodeError."""
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass


if __name__ == "__main__":
    url = f"http://localhost:{settings.APP_PORT}"
    safe_print("=" * 66)
    safe_print("   PikaMC Forensics Investigator - Standalone Edition")
    safe_print(f"   Phien ban: v{settings.VERSION} | Che do: Giam dinh READ-ONLY")
    safe_print("=" * 66)
    safe_print(f"[*] Server dang khoi dong: {settings.APP_HOST}:{settings.APP_PORT}")
    safe_print(f"[*] Web Dashboard: {url}")
    safe_print("[*] Dang tu dong mo trinh duyet web...")
    safe_print("[!] Nhan Ctrl + C hoac dong cua so nay de tat ung dung.")
    safe_print("=" * 66)

    # Khởi chạy thread mở trình duyệt
    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    # Chạy uvicorn với app instance trực tiếp (chuẩn PyInstaller)
    uvicorn.run(
        app,
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
        log_level=settings.LOG_LEVEL.lower(),
    )
