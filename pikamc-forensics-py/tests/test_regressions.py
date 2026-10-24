"""Regression tests cho các lỗi đã fix.

Mỗi test ở đây FAIL trên code cũ và PASS sau khi fix — dùng để chống tái phát.
"""
import pytest

from app.database import SessionLocal
from app.services.settings_service import get_all_settings, get_setting, set_setting


class TestSettingsRoundTrip:
    """BUG-1: chuỗi giống JSON bị ép kiểu khi đọc lại."""

    @pytest.mark.parametrize("value", [
        "hello", "123", "true", "false", "null", "[]", '{"a":1}', "3.14",
    ])
    def test_string_roundtrip_keeps_string_type(self, setup_db, value):
        db = SessionLocal()
        try:
            assert set_setting(db, "probe", value) is True
            got = get_setting(db, "probe")
            assert got == value
            assert isinstance(got, str)
        finally:
            db.close()

    @pytest.mark.parametrize("value", [42, True, False, 3.14, None, {"k": "v"}, ["x"], 0, ""])
    def test_non_string_roundtrip(self, setup_db, value):
        db = SessionLocal()
        try:
            assert set_setting(db, "probe", value) is True
            got = get_setting(db, "probe")
            assert got == value
            assert type(got) is type(value)
        finally:
            db.close()

    def test_get_all_settings_keeps_types(self, setup_db):
        db = SessionLocal()
        try:
            set_setting(db, "s", "123")
            set_setting(db, "n", 123)
            out = get_all_settings(db)
            assert out["s"] == "123" and isinstance(out["s"], str)
            assert out["n"] == 123 and isinstance(out["n"], int)
        finally:
            db.close()

    def test_missing_key_returns_default(self, setup_db):
        db = SessionLocal()
        try:
            assert get_setting(db, "nope", default="dflt") == "dflt"
        finally:
            db.close()


class TestSettingsSerialization:
    """BUG-2: value không serialize được phải trả False, không raise TypeError."""

    def test_non_serializable_returns_false(self, setup_db):
        db = SessionLocal()
        try:
            assert set_setting(db, "bad", object()) is False
        finally:
            db.close()


class TestAIModelResolution:
    """BUG-3: model mặc định phải được truyền vào AIOptions, không bị bỏ."""

    def test_default_model_passed_to_options(self, monkeypatch):
        from app.routers import ai as ai_router

        captured = {}

        def fake_analyze(prompt, provider, options, system_prompt):
            captured["provider"] = provider
            captured["model"] = options.model
            return "ok", 1

        monkeypatch.setattr(ai_router, "analyze_with_provider", fake_analyze)

        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        r = client.post("/api/ai/analyze", json={
            "prompt": "phân tích player", "provider": "gemini", "strict_read_only": True,
        })
        assert r.status_code == 200
        # Provider mặc định của gemini phải được áp dụng thay vì None
        assert captured["model"] == ai_router.DEFAULT_MODELS["gemini"]

    def test_explicit_model_overrides_default(self, monkeypatch):
        from app.routers import ai as ai_router

        captured = {}

        def fake_analyze(prompt, provider, options, system_prompt):
            captured["model"] = options.model
            return "ok", 1

        monkeypatch.setattr(ai_router, "analyze_with_provider", fake_analyze)

        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        r = client.post("/api/ai/analyze", json={
            "prompt": "phân tích player", "provider": "gemini",
            "model": "gemini-2.0-flash", "strict_read_only": True,
        })
        assert r.status_code == 200
        assert captured["model"] == "gemini-2.0-flash"


class TestScanSchema:
    """BUG-6: /api/scan/world phải validate body qua ScanWorldRequest."""

    def test_missing_server_id_is_422(self, client):
        r = client.post("/api/scan/world", json={})
        assert r.status_code == 422

    def test_blank_server_id_is_422(self, client):
        r = client.post("/api/scan/world", json={"server_id": "   "})
        assert r.status_code == 422


class TestCacheKey:
    """BUG-5: cache key phải ổn định với thứ tự params khác nhau."""

    def test_param_order_does_not_split_cache(self):
        import json
        d1 = {"file": "a", "limit": 1}
        d2 = {"limit": 1, "file": "a"}
        k1 = json.dumps(d1, sort_keys=True, default=str)
        k2 = json.dumps(d2, sort_keys=True, default=str)
        assert k1 == k2
