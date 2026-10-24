"""Tests cho hệ thống cấu hình PikaMC API Key và AI Models trực tiếp qua UI không cần .env."""
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.main import app
from app.config import get_settings
from app.database import SessionLocal
from app.models import Setting

client = TestClient(app)


def test_get_system_config():
    """Kiểm tra đọc cấu hình hệ thống (mặc định che key)."""
    res = client.get("/api/settings/config")
    assert res.status_code == 200
    data = res.json()

    assert "pikamc" in data
    assert "api_url" in data["pikamc"]
    assert "has_api_key" in data["pikamc"]
    assert "ai" in data
    assert "xai247" in data["ai"]
    assert "meta" in data["ai"]
    assert "gemini" in data["ai"]
    assert "openai" in data["ai"]
    assert "ollama" in data["ai"]


def test_get_system_config_reveal():
    """Kiểm tra đọc cấu hình với reveal=true."""
    res = client.get("/api/settings/config?reveal=true")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["pikamc"]["api_key"], str)


def test_update_system_config_dynamic_runtime():
    """Kiểm tra cập nhật cấu hình PikaMC và AI model mà không cần sửa file .env."""
    original_settings = get_settings()
    old_pika_key = original_settings.PIKAMC_API_KEY
    old_xai_model = original_settings.XAI247_MODEL

    try:
        payload = {
            "pikamc_api_key": "c7sp_test_updated_key_999",
            "pikamc_api_url": "https://hub.pikamc.vn",
            "xai247_model": "claude-3-5-sonnet",
            "default_provider": "xai247",
            "strict_read_only": True,
        }

        res = client.post("/api/settings/config", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "pikamc_api_key" in data["updated_keys"]
        assert "xai247_model" in data["updated_keys"]

        # Kiểm tra singleton in-memory đã được cập nhật ngay lập tức
        curr = get_settings()
        assert curr.PIKAMC_API_KEY == "c7sp_test_updated_key_999"
        assert curr.XAI247_MODEL == "claude-3-5-sonnet"

        # Kiểm tra SQLite đã lưu
        db = SessionLocal()
        try:
            row = db.get(Setting, "pikamc_api_key")
            assert row is not None
            assert "c7sp_test_updated_key_999" in row.value
        finally:
            db.close()
    finally:
        # Khôi phục lại cấu hình gốc
        restore_payload = {
            "pikamc_api_key": old_pika_key,
            "xai247_model": old_xai_model,
        }
        client.post("/api/settings/config", json=restore_payload)


def test_test_pika_connection_mocked():
    """Kiểm tra endpoint test kết nối PikaMC."""
    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "srv1", "name": "Survival 1"}]}

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        res = client.post("/api/settings/test-pika", json={"api_key": "c7sp_mock", "api_url": "https://hub.pikamc.vn"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["server_count"] == 1
        assert "thành công" in data["message"].lower()


def test_test_ai_connection_mocked():
    """Kiểm tra endpoint test kết nối AI provider."""
    with patch("app.services.ai_service.analyze_with_provider", return_value=("Kết nối AI thành công.", 120)):
        res = client.post("/api/settings/test-ai", json={"provider": "xai247", "model": "deepseek-v4.1-flash"})
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["provider"] == "xai247"
        assert "thành công" in data["response"]
