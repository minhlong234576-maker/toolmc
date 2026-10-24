"""Unit tests và integration tests cho XAI247 AI Gateway (xai247.xyz - model deepseek-v4.1-flash)."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.ai_service import AIOptions, analyze_with_provider, get_provider


def test_xai247_provider_configuration():
    get_settings.cache_clear()
    provider = get_provider("xai247")
    assert provider.name == "xai247"
    assert provider.default_model == "deepseek-v4.1-flash"
    assert provider.is_available() is True


def test_xai247_status_endpoint(client: TestClient):
    resp = client.get("/api/ai/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "xai247" in data
    xai = data["xai247"]
    assert xai["configured"] is True
    assert xai["default_model"] == "deepseek-v4.1-flash"
    assert xai["url"] == "https://xai247.xyz/v1"
    assert "models" in xai
    assert "deepseek-v4.1-flash" in xai["models"]


def test_xai247_analyze_mocked(client: TestClient):
    with patch("app.services.ai_service.XAI247Provider.analyze") as mock_analyze:
        mock_analyze.return_value = "Phân tích khách quan: Dữ liệu người chơi hoàn toàn hợp lệ."

        resp = client.post(
            "/api/ai/analyze",
            json={
                "prompt": "Đánh giá người chơi Alex có chỉ số khai khoáng bình thường.",
                "provider": "xai247",
                "model": "deepseek-v4.1-flash"
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "xai247"
        assert data["model"] == "deepseek-v4.1-flash"
        assert "hoàn toàn hợp lệ" in data["response"]
        assert data["strict_read_only"] is True


def test_xai247_blocked_dangerous_prompt(client: TestClient):
    # System strictly rejects dangerous prompt commands (ban player, kick player, delete)
    resp = client.post(
        "/api/ai/analyze",
        json={
            "prompt": "ban player X ngay lập tức!",
            "provider": "xai247",
            "model": "deepseek-v4.1-flash"
        }
    )
    assert resp.status_code == 400
    assert "chứa hành động bị cấm" in resp.json()["detail"].lower() or "read-only" in resp.json()["detail"].lower()


def test_xai247_live_direct_call():
    """Gọi trực tiếp live tới XAI247 API Gateway với model deepseek-v4.1-flash."""
    opts = AIOptions(model="deepseek-v4.1-flash", temperature=0.3, max_tokens=100)
    sys_prompt = "Bạn là trợ lý AI giám định độc lập."
    prompt = "Xác nhận kết nối test 1 câu ngắn gọn."
    text, duration = analyze_with_provider(prompt, "xai247", opts, sys_prompt)
    assert len(text) > 0
    assert duration > 0
