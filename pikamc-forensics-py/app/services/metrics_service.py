"""Tính toán metrics + classify verdict dựa trên THRESHOLDS."""
import logging
from typing import Any

from app.config import THRESHOLDS
from app.schemas import EvidenceItem, PlayerMetricsOut
from app.services import nbt_service

logger = logging.getLogger("pikamc.metrics")


def _num(stats: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Trích số từ stats dict theo nhiều naming convention."""
    if key in stats:
        return float(stats[key])
    lower = key.lower()
    for k, v in stats.items():
        if str(k).lower().replace("-", "_") == lower:
            return float(v)
    return default


def calculate_metrics(stats: dict[str, Any], nbt: dict[str, Any],
                      logs: list[dict[str, Any]]) -> PlayerMetricsOut:
    """Tính toàn bộ metrics từ: stats (PikaMC), nbt (player.dat), logs."""
    play_hours = _num(stats, "play_hours")
    if play_hours <= 0:
        play_minutes = _num(stats, "play_time_minutes")
        play_hours = play_minutes / 60.0 if play_minutes else _num(stats, "play_time_hours")
    if play_hours <= 0 and "play_time_ticks" in stats:
        play_hours = _num(stats, "play_time_ticks") / 72000.0
    if play_hours <= 0 and "play_time" in stats:
        # Minecraft ticks (72000 ticks = 1 hour)
        play_hours = _num(stats, "play_time") / 72000.0
    if play_hours <= 0 and "total_world_time" in stats:
        play_hours = _num(stats, "total_world_time") / 72000.0
    play_hours = max(round(play_hours, 2), 0.0)

    rare_ores_mined = _num(stats, "rare_ores")
    if rare_ores_mined <= 0:
        rare_ores_mined = (_num(stats, "diamond_ore_mined") + _num(stats, "emerald_ore_mined") +
                           _num(stats, "netherite_ore_mined") + _num(stats, "ancient_debris_mined") +
                           _num(stats, "deepslate_diamond_ore") + _num(stats, "diamond_ore"))

    rare_ores_per_hour = round(rare_ores_mined / play_hours, 2) if play_hours > 0 else 0.0

    kills = _num(stats, "player_kills")
    deaths = _num(stats, "deaths")
    kdr = round(kills / deaths, 2) if deaths > 0 else float(kills)

    km_traveled = _num(stats, "km_traveled")          # km
    distance_m = (_num(stats, "distance_traveled") if "distance_traveled" in stats else km_traveled * 1000)  # m
    km_per_hour = round((distance_m / 1000.0) / play_hours, 2) if play_hours > 0 else 0.0

    blocks_broken = _num(stats, "blocks_broken")
    blocks_per_hour = round(blocks_broken / play_hours, 2) if play_hours > 0 else 0.0

    # NBT inventory
    inventory = nbt.get("inventory", []) if isinstance(nbt, dict) else []
    rare_ores_in_inventory = nbt_service.count_rare_ores_inventory(inventory)

    flags = nbt.get("flags", {}) if isinstance(nbt, dict) else {}
    mayfly = bool(flags.get("mayfly", False))
    invulnerable = bool(flags.get("invulnerable", False))

    # Inventory illegal items
    illegal_items: list[str] = []
    for item in inventory:
        suspicious, reasons = nbt_service.is_suspicious_item(item)
        if suspicious:
            illegal_items.extend(reasons)

    # Logs
    fastbreak_logs = 0
    movement_violations = 0
    for log in logs:
        kind = str(log.get("type", log.get("event", ""))).lower()
        if "fastbreak" in kind:
            fastbreak_logs += 1
        if any(k in kind for k in ("speed", "fly", "step", "movementviolation", "speedhack", "nofall", "movement")):
            movement_violations += 1

    return PlayerMetricsOut(
        play_hours=round(play_hours, 2),
        rare_ores_per_hour=rare_ores_per_hour,
        kdr=kdr,
        km_per_hour=km_per_hour,
        blocks_per_hour=blocks_per_hour,
        rare_ores_in_inventory=rare_ores_in_inventory,
        fastbreak_logs_count=fastbreak_logs,
        movement_violations=movement_violations,
        mayfly=mayfly,
        invulnerable=invulnerable,
        illegal_items=illegal_items,
    )


def _score_from_thresholds(value: float, thresholds: dict[str, float]) -> int:
    """Chuyển value thành điểm (0-30): lớn hơn confirmed > suspicious cho điểm cao."""
    confirmed = thresholds.get("confirmed")
    suspicious = thresholds.get("suspicious")
    critical = thresholds.get("critical")
    # confirmed*2 trở lên (hoặc vọt ngưỡng critical) -> điểm nặng nhất
    if confirmed is not None and critical is not None and value >= max(confirmed * 2, critical):
        return 60
    # đạt ngưỡng confirmed -> chắc chắn có vi phạm
    if confirmed is not None and value >= confirmed:
        return 50
    # đạt ngưỡng suspicious -> 1 tín hiệu đáng ngờ cũng đủ đưa vào SUSPICIOUS
    if suspicious is not None and value >= suspicious:
        return 30
    return 0


def classify(metrics: PlayerMetricsOut) -> tuple[str, int, list[EvidenceItem]]:
    """Phân loại verdict + confidence (0-100) + danh sách bằng chứng."""
    evidence: list[EvidenceItem] = []
    score = 0

    # XRay
    x = _score_from_thresholds(metrics.rare_ores_per_hour, THRESHOLDS["xray"])
    if x:
        evidence.append(EvidenceItem(
            category="xray",
            description=f"Rare ores/h = {metrics.rare_ores_per_hour} (TB người chơi < 10)",
            severity="critical" if x >= 50 else "medium",
            value=str(metrics.rare_ores_per_hour),
        ))
        score += x

    # Fastbreak
    f = _score_from_thresholds(metrics.fastbreak_logs_count, THRESHOLDS["fastbreak"])
    if f:
        evidence.append(EvidenceItem(
            category="fastbreak",
            description=f"Fast-break violations: {metrics.fastbreak_logs_count}",
            severity="high" if f >= 50 else "medium",
            value=str(metrics.fastbreak_logs_count),
        ))
        score += f

    # Combat KDR
    k = _score_from_thresholds(metrics.kdr, THRESHOLDS["combat_kd"])
    if k:
        evidence.append(EvidenceItem(
            category="combat",
            description=f"KDR = {metrics.kdr} bất thường",
            severity="medium",
            value=str(metrics.kdr),
        ))
        score += k

    # Movement
    m = _score_from_thresholds(metrics.movement_violations, THRESHOLDS["movement"])
    if m:
        evidence.append(EvidenceItem(
            category="movement",
            description=f"Movement violations: {metrics.movement_violations} (speed/fly/step)",
            severity="high" if m >= 50 else "medium",
            value=str(metrics.movement_violations),
        ))
        score += m

    # Bot-like play hours bất thường
    b = _score_from_thresholds(metrics.play_hours, THRESHOLDS["bot_hours"])
    if b:
        evidence.append(EvidenceItem(
            category="bot",
            description=f"Play time bất thường: {metrics.play_hours} giờ",
            severity="low" if b < 50 else "medium",
            value=str(metrics.play_hours),
        ))
        score += b

    # Inventory nghiêm trọng (illegal items = bằng chứng trực tiếp)
    if metrics.illegal_items:
        evidence.append(EvidenceItem(
            category="inventory",
            description="; ".join(metrics.illegal_items[:5]),
            severity="critical",
            value=str(len(metrics.illegal_items)),
        ))
        score += 50  # illegal items là bằng chứng trực tiếp nhất

    if metrics.mayfly:
        evidence.append(EvidenceItem(
            category="movement",
            description="Mayfly (creative-only ability) bật trong survival",
            severity="critical",
            value="mayfly=true",
        ))
        score += 40

    if metrics.invulnerable:
        evidence.append(EvidenceItem(
            category="combat",
            description="Player Invulnerable (không thể chết)",
            severity="critical",
            value="invulnerable=true",
        ))
        score += 40

    score = min(score, 100)

    if score >= 70:
        verdict, confidence = "CONFIRMED_CHEAT", score
    elif score >= 30:
        verdict, confidence = "SUSPICIOUS", score
    else:
        verdict, confidence = "CLEAN", max(10, 100 - score)

    summary = _build_summary(verdict, confidence, evidence)
    return verdict, confidence, evidence


def _build_summary(verdict: str, confidence: int, evidence: list[EvidenceItem]) -> str:
    """Tạo câu tóm tắt tiếng Việt cho report."""
    if verdict == "CLEAN":
        return "Không phát hiện dấu hiệu gian lận rõ rệt từ dữ liệu hiện tại."
    cat_names = [e.category for e in evidence]
    if verdict == "SUSPICIOUS":
        return f"Có {len(evidence)} dấu hiệu đáng ngờ ({', '.join(cat_names)}) - khuyến nghị theo dõi tiếp."
    return f"Dữ liệu chỉ ra gian lận rõ ràng ({', '.join(cat_names)}) - mức độ chắc chắn {confidence}%."
