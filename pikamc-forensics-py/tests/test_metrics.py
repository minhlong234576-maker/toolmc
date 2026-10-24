"""Unit test: calculate_metrics + classify."""
from app.schemas import PlayerMetricsOut
from app.services.metrics_service import calculate_metrics, classify


class TestCalculateMetrics:
    def test_basic_rates(self):
        """Tính rate đúng theo play_hours."""
        stats = {"play_time_minutes": 120, "diamond_ore_mined": 100, "blocks_broken": 10000,
                 "distance_traveled": 50000}
        m = calculate_metrics(stats, {"ok": True, "inventory": [], "flags": {}}, [])
        assert m.play_hours == 2.0
        assert m.rare_ores_per_hour == 50.0
        assert m.blocks_per_hour == 5000.0
        assert m.km_per_hour == 25.0

    def test_zero_hours_no_divide(self):
        """Không chia cho 0 khi play_hours = 0."""
        m = calculate_metrics({}, {}, [])
        assert m.play_hours == 0.0
        assert m.rare_ores_per_hour == 0.0

    def test_logs_counting(self):
        logs = [{"type": "fastbreak"}, {"type": "fastbreak"}, {"type": "speed"}]
        m = calculate_metrics({}, {}, logs)
        assert m.fastbreak_logs_count == 2
        assert m.movement_violations == 1

    def test_nbt_flags(self):
        nbt = {"ok": True, "inventory": [], "flags": {"mayfly": True, "invulnerable": True}}
        m = calculate_metrics({}, nbt, [])
        assert m.mayfly is True
        assert m.invulnerable is True

    def test_movement_violation_case_insensitivity(self):
        """Kiểm tra nhận diện đúng MovementViolation / speed / fly bất kể hoa thường."""
        logs = [
            {"type": "MovementViolation"},
            {"type": "speedhack"},
            {"event": "fly"},
            {"type": "fastbreak"},
        ]
        m = calculate_metrics({}, {}, logs)
        assert m.movement_violations == 3
        assert m.fastbreak_logs_count == 1

    def test_unstackable_item_detection(self):
        """Phát hiện hack stack vật phẩm không thể xếp chồng (totem, kiếm, elytra...)."""
        from app.services.nbt_service import is_suspicious_item
        suspicious, reasons = is_suspicious_item({"id": "minecraft:totem_of_undying", "count": 64})
        assert suspicious is True
        assert any("không thể cộng dồn" in r for r in reasons)

        suspicious_pearl, reasons_pearl = is_suspicious_item({"id": "minecraft:ender_pearl", "count": 64})
        assert suspicious_pearl is True
        assert any("vượt stack" in r for r in reasons_pearl)


class TestClassify:
    def _m(self, **kw) -> PlayerMetricsOut:
        base = dict(play_hours=10.0, rare_ores_per_hour=0.0, kdr=0.0, km_per_hour=0.0,
                    blocks_per_hour=0.0, rare_ores_in_inventory=0, fastbreak_logs_count=0,
                    movement_violations=0)
        base.update(kw)
        return PlayerMetricsOut(**base)

    def test_clean(self):
        verdict, confidence, evidence = classify(self._m())
        assert verdict == "CLEAN"
        assert 0 <= confidence <= 100  # CLEAN: chắc chắn sạch = confidence cao
        assert evidence == []

    def test_xray_suspicious(self):
        verdict, conf, ev = classify(self._m(rare_ores_per_hour=30))
        assert verdict == "SUSPICIOUS"
        assert any(e.category == "xray" for e in ev)

    def test_confirmed_cheat(self):
        verdict, conf, ev = classify(self._m(rare_ores_per_hour=120, fastbreak_logs_count=20,
                                             illegal_items=["command_block x5"]))
        assert verdict == "CONFIRMED_CHEAT"
        assert conf >= 70
        assert len(ev) >= 2

    def test_mayfly_critical(self):
        verdict, conf, ev = classify(self._m(mayfly=True, play_hours=1))
        assert verdict in ("SUSPICIOUS", "CONFIRMED_CHEAT")
        assert any(e.severity == "critical" for e in ev)

    def test_confidence_bounds(self):
        _, conf, _ = classify(self._m(rare_ores_per_hour=999, fastbreak_logs_count=999,
                                      kdr=999, movement_violations=999, play_hours=999))
        assert 0 <= conf <= 100
