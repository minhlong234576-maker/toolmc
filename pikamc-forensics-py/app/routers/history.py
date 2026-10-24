"""Thống kê tổng hợp từ SQLite (SQL aggregate)."""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Investigation, Scan
from app.schemas import StatsOverview, TimelinePoint, TopOffender

logger = logging.getLogger("pikamc.history")
router = APIRouter(prefix="/api/stats", tags=["history"])


@router.get("/overview", response_model=StatsOverview)
async def overview(db: Session = Depends(get_db)) -> StatsOverview:
    """Thống kê tổng: tổng điều tra, scan, cheat, cheat rate, verdict phổ biến."""
    total_inv = db.query(func.count(Investigation.id)).scalar() or 0
    total_cheats = db.query(func.count(Investigation.id)).filter(
        Investigation.verdict == "CONFIRMED_CHEAT").scalar() or 0
    total_sus = db.query(func.count(Investigation.id)).filter(
        Investigation.verdict == "SUSPICIOUS").scalar() or 0
    total_clean = total_inv - total_cheats - total_sus

    total_scans = db.query(func.count(Scan.id)).scalar() or 0
    last_scan = db.query(func.max(Scan.created_at)).scalar()

    # Verdict phổ biến nhất (investigations) — GROUP BY verdict, COUNT desc
    most_common = (db.query(Investigation.verdict, func.count(Investigation.id).label("c"))
                   .group_by(Investigation.verdict)
                   .order_by(func.count(Investigation.id).desc())
                   .first())
    mc_verdict = most_common[0] if most_common else "CLEAN"

    # Cheat rate: số CONFIRMED_CHEAT / tổng số investigation (%)
    cheat_rate = round((total_cheats / max(total_inv, 1)) * 100, 2)

    return StatsOverview(
        total_investigations=total_inv,
        total_scans=total_scans,
        total_cheats_found=total_cheats,
        cheat_rate=cheat_rate,
        most_common_verdict=mc_verdict,
        last_scan_at=last_scan,
    )


@router.get("/timeline", response_model=list[TimelinePoint])
async def timeline(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)) -> list[TimelinePoint]:
    """Số cheat phát hiện theo ngày (GROUP BY date)."""
    rows = (db.query(
            func.date(Investigation.created_at).label("day"),
            func.count(Investigation.id).label("c"))
            .filter(Investigation.verdict == "CONFIRMED_CHEAT")
            .group_by(func.date(Investigation.created_at))
            .order_by(func.date(Investigation.created_at).desc())
            .limit(days).all())
    return [TimelinePoint(date=str(r.day), cheat_count=int(r.c)) for r in rows]


@router.get("/top_offenders", response_model=list[TopOffender])
async def top_offenders(limit: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)) -> list[TopOffender]:
    """Players bị flag nhiều nhất: GROUP BY player_uuid, COUNT + MAX confidence."""
    rows = (db.query(
            func.max(Investigation.player_name).label("name"),
            Investigation.player_uuid,
            func.count(Investigation.id).label("n"),
            func.max(Investigation.confidence).label("maxc"))
            .group_by(Investigation.player_uuid)
            .order_by(func.count(Investigation.id).desc())
            .limit(limit).all())
    out: list[TopOffender] = []
    for name, uuid, n, maxc in rows:
        worst = (db.query(Investigation.verdict)
                 .filter(Investigation.player_uuid == uuid)
                 .order_by(Investigation.confidence.desc()).first())
        last = (db.query(func.max(Investigation.created_at))
                .filter(Investigation.player_uuid == uuid).scalar())
        out.append(TopOffender(player_name=name, player_uuid=uuid, flag_count=int(n),
                               max_confidence=int(maxc or 0),
                               worst_verdict=worst[0] if worst else "CLEAN",
                               last_seen=last))
    return out
