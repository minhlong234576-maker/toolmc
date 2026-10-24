"""Tests cho việc tích hợp dữ liệu real-time (world data, logs, user data) vào Trợ lý AI Giám định Chuyên sâu."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.schemas import AIAnalyzeRequest
from app.routers.ai import build_realtime_forensics_context
from app.database import get_db

client = TestClient(app)


@pytest.mark.anyio
async def test_build_realtime_context_world_logs_player():
    """Kiểm tra build_realtime_forensics_context tổng hợp đủ 3 khối dữ liệu."""
    req = AIAnalyzeRequest(
        prompt="Phân tích tình trạng gian lận của người chơi",
        server_id="3fe38490",
        player_name="TestPlayer",
        player_uuid="00000000-0000-0000-0009-01f462a22855",
        include_world_data=True,
        include_logs=True,
        include_player_data=True,
        provider="xai247",
    )

    mock_world = {
        "server_id": "3fe38490",
        "level_name": "world_survival",
        "world_age_days": 12.5,
        "world_age_hours": 300.0,
        "time_ticks": 18000,
        "time_of_day_phase": "Ban đêm",
        "game_type": "Sinh tồn",
        "difficulty": "Khó",
        "player_data_files": 45,
        "player_stats_files": 45,
    }

    mock_pulse = {
        "server_id": "3fe38490",
        "online_count": 8,
        "server_reported_online": 8,
        "resources": {
            "cpu_percent": 14.5,
            "memory_mb": 4096,
            "memory_limit_mb": 8192,
            "memory_percent": 50.0,
            "disk_mb": 15000,
        },
        "online_players": [{"name": "TestPlayer", "uuid": "00000000-0000-0000-0009-01f462a22855"}],
    }

    mock_logs = [
        {"timestamp": "12:00:01", "type": "movement", "player": "TestPlayer", "message": "moved too quickly! 15.2 blocks"},
        {"timestamp": "12:00:05", "type": "fastbreak", "player": "TestPlayer", "message": "broke 12 ancient_debris in 3s"},
    ]

    mock_stats = {
        "play_hours": 10.0,
        "rare_ores": 120.0,
        "player_kills": 15,
        "deaths": 1,
        "km_traveled": 80.0,
        "blocks_broken": 5000,
    }

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        with patch("app.services.pika_service.pika_service.get_world_overview", AsyncMock(return_value=mock_world)), \
             patch("app.services.pika_service.pika_service.get_realtime_pulse", AsyncMock(return_value=mock_pulse)), \
             patch("app.services.pika_service.pika_service.get_logs", AsyncMock(return_value=mock_logs)), \
             patch("app.services.pika_service.pika_service.get_player_stats", AsyncMock(return_value=mock_stats)), \
             patch("app.services.pika_service.pika_service.download_file", AsyncMock(return_value=b"fake_dat")):

            context = await build_realtime_forensics_context(req, db)

            # Kiểm tra các khối dữ liệu
            assert "[DỮ LIỆU REAL-TIME FORENSICS HỆ THỐNG - GIÁM ĐỊNH CHUYÊN SÂU]" in context
            assert "THẾ GIỚI & TÀI NGUYÊN MÁY CHỦ THỜI GIAN THỰC" in context
            assert "world_survival" in context
            assert "12.5 ngày" in context
            assert "NHẬT KÝ SERVER & CẢNH BÁO VI PHẠM THỜI GIAN THỰC" in context
            assert "moved too quickly" in context
            assert "DỮ LIỆU NGƯỜI CHƠI & TÚI ĐỒ NBT" in context
            assert "TestPlayer" in context
    finally:
        db.close()


def test_ai_analyze_endpoint_with_realtime_context():
    """Kiểm tra gọi POST /api/ai/analyze kèm cờ context thời gian thực."""
    payload = {
        "prompt": "Đánh giá chi tiết người chơi TestPlayer",
        "server_id": "3fe38490",
        "player_name": "TestPlayer",
        "include_world_data": True,
        "include_logs": True,
        "include_player_data": True,
        "provider": "xai247",
        "model": "deepseek-v4.1-flash",
        "strict_read_only": True,
    }

    with patch("app.services.ai_service.XAI247Provider.analyze", return_value="Báo cáo giám định: Đối tượng có chỉ số đào khoáng 12 ores/h, không có dấu hiệu hack."):
        res = client.post("/api/ai/analyze", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "xai247"
        assert "Báo cáo giám định" in data["response"]
        assert data["strict_read_only"] is True
