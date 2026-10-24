"""FastAPI TestClient + SQLite in-memory: test toàn bộ API."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import engine, Base


@pytest.fixture(autouse=True)
def setup_db():
    """Tạo lại schema sạch trước mỗi test (DuckDB-free, dùng chính engine SQLite)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "uptime" in data and "version" in data and "db_size_kb" in data


class TestPika:
    def test_live_activity(self, client, monkeypatch):
        from app.routers import pika as pika_router

        async def fake_act(server_id, limit=30):
            return {"server_id": server_id, "events_count": 1, "events": [{"type": "join", "player": "Steve", "time": "12:00:00"}]}

        monkeypatch.setattr(pika_router.pika_service, "get_live_activity", fake_act)
        r = client.get("/api/servers/s1/live-activity")
        assert r.status_code == 200
        assert r.json()["events_count"] == 1
        assert r.json()["events"][0]["player"] == "Steve"

    def test_players_force_refresh(self, client, monkeypatch):
        from app.routers import pika as pika_router

        async def fake_players(server_id, ttl=60, force_refresh=False):
            return [{"name": "Alex", "uuid": "u-alex", "status": "online"}]

        monkeypatch.setattr(pika_router.pika_service, "get_player_list", fake_players)
        r = client.get("/api/servers/s1/players?force_refresh=true")
        assert r.status_code == 200
        assert r.json()["count"] == 1
        assert r.json()["players"][0]["name"] == "Alex"

    def test_realtime_pulse(self, client, monkeypatch):
        from app.routers import pika as pika_router

        async def fake_pulse(server_id):
            return {
                "server_id": server_id,
                "timestamp": 123456789,
                "resources": {"cpu_percent": 25.5, "memory_mb": 1024},
                "online_count": 2,
                "online_players": [{"name": "Alex", "uuid": "u-alex"}],
                "events": [],
                "world": {"world_age_days": 100.5}
            }

        monkeypatch.setattr(pika_router.pika_service, "get_realtime_pulse", fake_pulse)
        r = client.get("/api/servers/s1/realtime-pulse")
        assert r.status_code == 200
        data = r.json()
        assert data["online_count"] == 2
        assert data["resources"]["cpu_percent"] == 25.5

    def test_world_overview(self, client, monkeypatch):
        from app.routers import pika as pika_router

        async def fake_world(server_id):
            return {
                "server_id": server_id,
                "level_name": "world",
                "world_age_days": 1208.7,
                "player_data_files": 25
            }

        monkeypatch.setattr(pika_router.pika_service, "get_world_overview", fake_world)
        r = client.get("/api/servers/s1/world-overview")
        assert r.status_code == 200
        data = r.json()
        assert data["level_name"] == "world"
        assert data["player_data_files"] == 25

    def test_log_files(self, client, monkeypatch):
        from app.routers import pika as pika_router

        async def fake_log_files(server_id):
            return [
                {"name": "latest.log", "size_kb": 12.5, "is_current": True},
                {"name": "2026-10-06-1.log.gz", "size_kb": 4.2, "is_current": False}
            ]

        monkeypatch.setattr(pika_router.pika_service, "get_log_files", fake_log_files)
        r = client.get("/api/servers/s1/logs/files")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert data["files"][0]["name"] == "latest.log"

    def test_logs_view(self, client, monkeypatch):
        from app.routers import pika as pika_router

        async def fake_read_log(server_id, filename="latest.log", limit=300, category=None, search=None):
            return {
                "filename": filename,
                "total_lines": 50,
                "matched_lines": 1,
                "violations_count": 1,
                "lines": [{"line_no": 1, "text": "moved too quickly!", "category": "movement"}]
            }

        monkeypatch.setattr(pika_router.pika_service, "read_log_file", fake_read_log)
        r = client.get("/api/servers/s1/logs/view?file=latest.log&category=movement")
        assert r.status_code == 200
        data = r.json()
        assert data["violations_count"] == 1
        assert data["lines"][0]["category"] == "movement"



class TestInvestigate:
    def test_investigate_clean(self, client, monkeypatch):
        """Mock PikaMC service nên test chạy offline."""
        import asyncio
        from app.routers import investigate as inv

        async def fake_stats(server_id, uuid, ttl=300):
            return {"play_time_minutes": 60}
        async def fake_logs(server_id, limit=100, player_name=None, ttl=60):
            return []
        async def fake_download(server_id, uuid, filename="playerdata.dat"):
            return None
        monkeypatch.setattr(inv.pika_service, "get_player_stats", fake_stats)
        monkeypatch.setattr(inv.pika_service, "get_logs", fake_logs)
        monkeypatch.setattr(inv.pika_service, "download_file", fake_download)

        r = client.post("/api/investigate", json={"server_id": "s1", "player_name": "A",
                                                  "player_uuid": "u1"})
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] in ("CLEAN", "SUSPICIOUS", "CONFIRMED_CHEAT")
        assert data["id"] is not None  # ✅ đã lưu SQLite

    def test_history_after_investigate(self, client, monkeypatch):
        import asyncio
        from app.routers import investigate as inv

        async def fake_stats(server_id, uuid, ttl=300):
            return {}
        async def fake_logs(server_id, limit=100, player_name=None, ttl=60):
            return []
        async def fake_download(server_id, uuid, filename="playerdata.dat"):
            return None
        monkeypatch.setattr(inv.pika_service, "get_player_stats", fake_stats)
        monkeypatch.setattr(inv.pika_service, "get_logs", fake_logs)
        monkeypatch.setattr(inv.pika_service, "download_file", fake_download)

        client.post("/api/investigate", json={"server_id": "s1", "player_name": "A", "player_uuid": "u1"})
        h = client.get("/api/investigate/history").json()
        assert h["count"] == 1
        assert h["items"][0]["player_name"] == "A"

    def test_delete_record(self, client, monkeypatch):
        def fake_stats(server_id, uuid): return {}
        async def fake_download(server_id, uuid, filename="playerdata.dat"): return None
        from app.routers import investigate as inv
        monkeypatch.setattr(inv.pika_service, "get_player_stats", fake_stats)
        monkeypatch.setattr(inv.pika_service, "get_logs", fake_stats)
        monkeypatch.setattr(inv.pika_service, "download_file", fake_download)
        cid = client.post("/api/investigate", json={"server_id": "s1", "player_name": "A", "player_uuid": "u1"}).json()["id"]
        assert client.delete(f"/api/investigate/{cid}").json()["deleted"] is True
        assert client.get(f"/api/investigate/{cid}").status_code == 404


class TestScan:
    def test_scan_world(self, client, monkeypatch):
        from app.routers import scan as scan_router

        class FakePika:
            async def get_player_list(self, server_id, ttl=120):
                return [{"name": "A", "uuid": "u1"}]
        monkeypatch.setattr(scan_router, "pika_service", FakePika())

        import app.routers.investigate as inv
        from app.services.pika_service import pika_service as real_pika
        monkeypatch.setattr(inv, "pika_service", FakePika())

        r = client.post("/api/scan/world", json={"server_id": "s1"})
        assert r.status_code == 200
        scan_id = r.json()["scan_id"]

        # BackgroundTask chạy trong TestClient — poll status
        st = client.get(f"/api/scan/{scan_id}/status").json()
        assert st["id"] == scan_id
        assert st["status"] in ("processing", "completed")

    def test_scan_results_filter(self, client):
        """SQL filter verdict từ scan_results."""
        from app.database import SessionLocal
        from app.models import Scan, ScanResult
        db = SessionLocal()
        scan = Scan(server_id="s1", status="completed", progress=100,
                    cheat_count=1, suspicious_count=1, clean_count=1, total_players=3, scanned_players=3)
        db.add(scan); db.commit(); db.refresh(scan)
        for v, c in [("CONFIRMED_CHEAT", 90), ("SUSPICIOUS", 45), ("CLEAN", 10)]:
            db.add(ScanResult(scan_id=scan.id, player_name=f"P{v}", player_uuid=f"u{v}",
                              verdict=v, confidence=c, metrics_json="{}", evidence_json="[]"))
        db.commit(); db.close()

        res = client.get(f"/api/scan/{scan.id}/results?verdict=CONFIRMED_CHEAT").json()
        assert res["count"] == 1
        assert res["items"][0]["verdict"] == "CONFIRMED_CHEAT"
        all_res = client.get(f"/api/scan/{scan.id}/results?sort=confidence").json()
        assert all_res["items"][0]["player_name"] == "PCONFIRMED_CHEAT"  # sort desc

    def test_scan_stop(self, client):
        """Dừng scan khi đang chạy."""
        from app.database import SessionLocal
        from app.models import Scan
        db = SessionLocal()
        scan = Scan(server_id="s1", status="processing", progress=10)
        db.add(scan); db.commit(); db.refresh(scan)
        sid = scan.id
        db.close()

        res = client.post(f"/api/scan/{sid}/stop").json()
        assert res["status"] == "stopping"



class TestAI:
    def test_prompt_blocked(self, client):
        """Prompt chứa dangerous keyword -> 400 khi strict_read_only=True."""
        r = client.post("/api/ai/analyze", json={"prompt": "ban player X ngay", "provider": "gemini"})
        assert r.status_code == 400

    def test_unconfigured_provider(self, client):
        """Provider chưa có API key -> 503."""
        r = client.post("/api/ai/analyze", json={"prompt": "phân tích", "provider": "gemini",
                                                 "strict_read_only": False})
        assert r.status_code == 503

    def test_ai_history_empty(self, client):
        r = client.get("/api/ai/history").json()
        assert r["count"] == 0

    def test_ai_status(self, client):
        r = client.get("/api/ai/status")
        assert r.status_code == 200
        data = r.json()
        assert "ollama" in data and "gemini" in data and "openai" in data and "meta" in data and "xai247" in data
        assert data["meta"]["url"] == "https://api.meta.ai/v1"
        assert data["xai247"]["url"] == "https://xai247.xyz/v1"

    def test_meta_and_xai247_providers_registered(self):
        from app.services.ai_service import get_provider
        meta_p = get_provider("meta")
        assert meta_p.name == "meta"
        xai_p = get_provider("xai247")
        assert xai_p.name == "xai247"
        llama_p = get_provider("llama")
        assert llama_p.name == "meta"


class TestStats:
    def _seed(self, db):
        from app.models import Investigation, Scan
        db.add(Investigation(server_id="s1", player_name="A", player_uuid="u1",
                             verdict="CONFIRMED_CHEAT", confidence=80, metrics_json="{}",
                             evidence_json='[]'))
        db.add(Investigation(server_id="s1", player_name="A", player_uuid="u1",
                             verdict="SUSPICIOUS", confidence=40, metrics_json="{}",
                             evidence_json='[]'))
        db.add(Scan(server_id="s1", status="completed"))
        db.commit()
        db.close()

    def test_overview(self, client):
        from app.database import SessionLocal
        self._seed(SessionLocal())
        o = client.get("/api/stats/overview").json()
        assert o["total_investigations"] == 2
        assert o["total_scans"] == 1
        assert o["total_cheats_found"] == 1
        assert o["cheat_rate"] == 50.0
        assert o["most_common_verdict"] in ("CONFIRMED_CHEAT", "SUSPICIOUS")

    def test_timeline(self, client):
        from app.database import SessionLocal
        self._seed(SessionLocal())
        tl = client.get("/api/stats/timeline?days=30").json()
        assert len(tl) == 1
        assert tl[0]["cheat_count"] == 1

    def test_top_offenders(self, client):
        from app.database import SessionLocal
        from app.models import Investigation
        db = SessionLocal()
        self._seed(db)
        # Thêm 1 lần điều tra nữa nhưng tên người chơi bị đổi (ví dụ có thêm clan prefix)
        db = SessionLocal()
        db.add(Investigation(server_id="s1", player_name="[Clan] A", player_uuid="u1",
                             verdict="CONFIRMED_CHEAT", confidence=95, metrics_json="{}",
                             evidence_json='[]'))
        db.commit()
        db.close()

        top = client.get("/api/stats/top_offenders").json()
        assert len(top) == 1  # Không bị tách làm 2 người dù tên khác nhau
        assert top[0]["player_uuid"] == "u1"
        assert top[0]["flag_count"] == 3
        assert top[0]["max_confidence"] == 95
        assert top[0]["worst_verdict"] == "CONFIRMED_CHEAT"


class TestFrontend:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "PikaMC Forensics" in r.text
        assert "Lịch sử" in r.text or "tab3" in r.text
