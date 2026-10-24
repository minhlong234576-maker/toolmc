"""Unit tests cho tính năng điều khiển nguồn máy chủ và quản lý lịch trình tự động (Schedules)."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def test_describe_cron():
    from app.services.pika_service import pika_service
    assert "04:00 AM" in pika_service._describe_cron("0 4 * * *")
    assert "6 tiếng" in pika_service._describe_cron("0 */6 * * *")
    assert "12:00 PM" in pika_service._describe_cron("0 12 * * *")
    assert "Hàng ngày lúc 02:30" in pika_service._describe_cron("30 2 * * *")


def test_power_action_invalid(client: TestClient):
    resp = client.post("/api/servers/3fe38490/power", json={"action": "explode"})
    assert resp.status_code == 422  # Pydantic validation


def test_power_action_success(client: TestClient):
    with patch("app.services.pika_service.pika_service._post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = (204, {})
        resp = client.post("/api/servers/3fe38490/power", json={"action": "restart"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["action"] == "restart"
        mock_post.assert_called_once_with("/api/client/servers/3fe38490/power", json_data={"action": "restart"})


def test_console_command(client: TestClient):
    with patch("app.services.pika_service.pika_service._post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = (200, {})
        resp = client.post("/api/servers/3fe38490/command", json={"command": "/say Test broadcast"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["command"] == "say Test broadcast"
        mock_post.assert_called_once_with("/api/client/servers/3fe38490/command", json_data={"command": "say Test broadcast"})


def test_get_schedules(client: TestClient):
    mock_schedules_resp = {
        "schedules": {
            "data": [
                {
                    "uuid": "sched-123",
                    "name": "Auto Restart 4AM",
                    "enabled": True,
                    "triggers": [{"type": "cron", "schedule": "0 4 * * *"}],
                    "last_run": None,
                    "created": "2026-10-24T00:00:00Z"
                }
            ]
        }
    }
    with patch("app.services.pika_service.pika_service._get", new_callable=AsyncMock) as mock_get:
        # First call gets schedules, second call gets steps
        mock_get.side_effect = [
            mock_schedules_resp,
            {"schedule_steps": {"data": [{"order": 0, "action": {"type": "send_power", "action": "restart"}}]}}
        ]
        resp = client.get("/api/servers/3fe38490/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        sched = data["schedules"][0]
        assert sched["uuid"] == "sched-123"
        assert "04:00 AM" in sched["cron_desc"]
        assert len(sched["steps"]) == 1


def test_create_schedule_invalid_cron(client: TestClient):
    resp = client.post(
        "/api/servers/3fe38490/schedules",
        json={"name": "Bad Cron", "cron": "invalid cron format", "action_type": "restart"}
    )
    assert resp.status_code == 400
    assert "Biểu thức Cron không hợp lệ" in resp.json()["detail"]


def test_create_schedule_success(client: TestClient):
    with patch("app.services.pika_service.pika_service._post", new_callable=AsyncMock) as mock_post:
        # Schedule creation returns 200, step creation returns 200
        mock_post.side_effect = [
            (200, {"schedule": {"uuid": "new-sched-456"}}),
            (200, {"schedule_step": {"uuid": "step-1"}})
        ]
        resp = client.post(
            "/api/servers/3fe38490/schedules",
            json={
                "name": "Restart đêm",
                "cron": "0 4 * * *",
                "action_type": "restart",
                "enabled": True
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["uuid"] == "new-sched-456"
        assert "04:00 AM" in data["cron_desc"]


def test_trigger_schedule(client: TestClient):
    with patch("app.services.pika_service.pika_service._post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = (200, {})
        resp = client.post("/api/servers/3fe38490/schedules/sched-123/trigger")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


def test_toggle_schedule(client: TestClient):
    with patch("app.services.pika_service.pika_service._patch", new_callable=AsyncMock) as mock_patch:
        mock_patch.return_value = (200, {})
        resp = client.patch("/api/servers/3fe38490/schedules/sched-123/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["enabled"] is False


def test_delete_schedule(client: TestClient):
    with patch("app.services.pika_service.pika_service._delete", new_callable=AsyncMock) as mock_del:
        mock_del.return_value = (200, {})
        resp = client.delete("/api/servers/3fe38490/schedules/sched-123")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

