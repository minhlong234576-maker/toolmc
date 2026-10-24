"""Service quản lý bảng settings (key-value trong SQLite).

Cho phép frontend lưu/tải cấu hình DB thay vì chỉ localStorage.
"""
import logging
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Setting

logger = logging.getLogger("pikamc.settings_service")


def _decode(text: str) -> Any:
    """Giải mã JSON; nếu không phải JSON hợp lệ thì coi là chuỗi thô (dữ liệu cũ)."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    """Đọc giá trị setting; tự parse JSON nếu có thể."""
    row = db.get(Setting, key)
    if row is None:
        return default
    return _decode(row.value)


def set_setting(db: Session, key: str, value: Any) -> bool:
    """Ghi setting (upsert) - luôn lưu dạng JSON để round-trip giữ nguyên kiểu.

    Mọi giá trị (kể cả chuỗi) đều được JSON-encode trước khi ghi, nhờ đó
    ``get_setting`` trả lại đúng kiểu ban đầu. Chuỗi dạng "123"/"true"/"null"
    không còn bị ép sang int/bool/None.
    """
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.error("Ghi setting %s thất bại - value không serialize được: %s", key, e)
        return False
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=text)
        db.add(row)
    else:
        row.value = text
    db.commit()
    return True


def get_all_settings(db: Session) -> dict[str, Any]:
    """Đọc toàn bộ settings thành dict."""
    rows = db.query(Setting).all()
    return {row.key: _decode(row.value) for row in rows}


def mask_key(val: str | None) -> str:
    """Ẩn bớt ký tự của API Key để hiển thị an toàn trên UI (VD: sk-xai-...Ttou)."""
    if not val or not val.strip():
        return ""
    s = val.strip()
    if len(s) <= 8:
        return "••••••••"
    return f"{s[:6]}...{s[-4:]}"


def update_env_file(key_values: dict[str, str]) -> bool:
    """Cập nhật an toàn các biến môi trường vào file .env, giữ nguyên chú thích và cấu trúc."""
    from pathlib import Path

    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"

    try:
        content = ""
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")

        lines = content.splitlines()
        remaining = dict(key_values)
        updated_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped or "=" not in stripped:
                updated_lines.append(line)
                continue
            k, _, _ = line.partition("=")
            key_name = k.strip()
            if key_name in remaining:
                new_val = remaining.pop(key_name)
                updated_lines.append(f"{key_name}={new_val}")
            else:
                updated_lines.append(line)

        # Thêm các key mới chưa có trong file .env
        for key_name, new_val in remaining.items():
            updated_lines.append(f"{key_name}={new_val}")

        env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
        logger.info("Đã cập nhật .env với %d biến môi trường", len(key_values))
        return True
    except Exception as e:
        logger.error("Lỗi khi ghi file .env: %s", e)
        return False


def apply_runtime_settings(updates: dict[str, Any]) -> None:
    """Cập nhật các giá trị cấu hình trực tiếp vào in-memory Settings và đồng bộ .env."""
    from app.config import get_settings

    settings = get_settings()

    field_map = {
        "pikamc_api_key": "PIKAMC_API_KEY",
        "pikamc_api_url": "PIKAMC_API_URL",
        "gemini_api_key": "GEMINI_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "ollama_host": "OLLAMA_HOST",
        "meta_api_key": "META_API_KEY",
        "meta_api_url": "META_API_URL",
        "meta_model": "META_MODEL",
        "xai247_api_key": "XAI247_API_KEY",
        "xai247_api_url": "XAI247_API_URL",
        "xai247_model": "XAI247_MODEL",
    }

    env_updates: dict[str, str] = {}
    reset_pika = False

    for k, v in updates.items():
        if v is None:
            continue
        v_str = str(v).strip()
        env_attr = field_map.get(k)
        if env_attr and hasattr(settings, env_attr):
            setattr(settings, env_attr, v_str)
            env_updates[env_attr] = v_str
            if "pikamc" in k:
                reset_pika = True

    if reset_pika:
        from app.services.pika_service import pika_service
        try:
            pika_service._client = None
        except Exception:
            pass

    if env_updates:
        update_env_file(env_updates)

