"""Điều tra đơn lẻ 1 player + lưu SQLite."""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Investigation
from app.schemas import (EvidenceItem, InvestigateRequest, InvestigateResponse,
                         PlayerMetricsOut)
from app.services.metrics_service import calculate_metrics, classify
from app.services.pika_service import pika_service

logger = logging.getLogger("pikamc.investigate")
router = APIRouter(prefix="/api/investigate", tags=["investigate"])


async def run_investigation(server_id: str, player_name: str, player_uuid: str,
                            db: Session | None = None) -> dict[str, object]:
    """Pipeline: fetch logs + stats + NBT -> metrics -> classify -> (lưu DB).

    Nếu db=None (luồng scan background) chỉ tính, không lưu.
    """
    try:
        stats = await pika_service.get_player_stats(server_id, player_uuid)
        logs = await pika_service.get_logs(server_id, limit=100, player_name=player_name)
        raw_dat = await pika_service.download_file(server_id, player_uuid, "playerdata.dat")
        from app.services.nbt_service import parse_player_dat
        nbt = parse_player_dat(raw_dat) if raw_dat else {"ok": False, "inventory": [], "flags": {}}
    except Exception as e:  # noqa: BLE001 — mọi lỗi fetch đều được xử lý an toàn phía dưới
        logger.error("Fetch dữ liệu %s/%s lỗi: %s", server_id, player_uuid, e)
        stats, logs, nbt = {}, [], {"ok": False, "inventory": [], "flags": {}}

    metrics: PlayerMetricsOut = calculate_metrics(stats, nbt, logs)
    verdict, confidence, evidence = classify(metrics)

    summary = evidence[0].description if evidence else "Không phát hiện dấu hiệu gian lận đáng ngờ."
    result: dict[str, object] = {
        "server_id": server_id,
        "player_name": player_name,
        "player_uuid": player_uuid,
        "verdict": verdict,
        "confidence": confidence,
        "metrics": metrics.model_dump(),
        "evidence": [e.model_dump() for e in evidence],
        "summary": summary,
    }

    if db is not None:
        row = Investigation(
            server_id=server_id, player_name=player_name, player_uuid=player_uuid,
            verdict=verdict, confidence=confidence,
            metrics_json=json.dumps(metrics.model_dump(), ensure_ascii=False),
            evidence_json=json.dumps([e.model_dump() for e in evidence], ensure_ascii=False),
            summary=summary,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        result["id"] = row.id
        result["created_at"] = row.created_at

    return result


@router.post("", response_model=InvestigateResponse)
async def investigate(req: InvestigateRequest, db: Session = Depends(get_db)) -> InvestigateResponse:
    """Điều tra 1 player, lưu lịch sử vào SQLite."""
    result = await run_investigation(req.server_id, req.player_name, req.player_uuid, db=db)
    m = PlayerMetricsOut(**result["metrics"])  # type: ignore[arg-type]
    return InvestigateResponse(
        id=result.get("id"),  # type: ignore[arg-type]
        server_id=result["server_id"],  # type: ignore[arg-type]
        player_name=result["player_name"],  # type: ignore[arg-type]
        player_uuid=result["player_uuid"],  # type: ignore[arg-type]
        verdict=result["verdict"],  # type: ignore[arg-type]
        confidence=result["confidence"],  # type: ignore[arg-type]
        metrics=m,
        evidence=[EvidenceItem(**e) for e in result["evidence"]],  # type: ignore[misc]
        summary=str(result.get("summary", "")),
        created_at=result.get("created_at"),  # type: ignore[arg-type]
    )


@router.get("/history")
async def history(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, object]:
    """Lịch sử điều tra (mới nhất trước) - load từ SQLite."""
    rows = db.query(Investigation).order_by(Investigation.created_at.desc()).limit(limit).all()
    return {
        "count": len(rows),
        "items": [{
            "id": r.id, "server_id": r.server_id, "player_name": r.player_name,
            "player_uuid": r.player_uuid, "verdict": r.verdict, "confidence": r.confidence,
            "metrics": json.loads(r.metrics_json), "evidence": json.loads(r.evidence_json),
            "summary": r.summary, "created_at": r.created_at,
        } for r in rows],
    }


@router.get("/{investigation_id}")
async def detail(investigation_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    """Chi tiết 1 lần điều tra."""
    r = db.get(Investigation, investigation_id)
    if r is None:
        raise HTTPException(404, f"Không tìm thấy investigation #{investigation_id}")
    return {
        "id": r.id, "server_id": r.server_id, "player_name": r.player_name,
        "player_uuid": r.player_uuid, "verdict": r.verdict, "confidence": r.confidence,
        "metrics": json.loads(r.metrics_json), "evidence": json.loads(r.evidence_json),
        "summary": r.summary, "created_at": r.created_at,
    }


@router.delete("/{investigation_id}")
async def delete_record(investigation_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    """Xóa bản ghi lịch sử LOCAL trong SQLite - KHÔNG đụng server Minecraft."""
    r = db.get(Investigation, investigation_id)
    if r is None:
        raise HTTPException(404, f"Không tìm thấy investigation #{investigation_id}")
    db.delete(r)
    db.commit()
    logger.info("Đã xóa investigation #%d (chỉ local)", investigation_id)
    return {"deleted": True, "id": investigation_id,
            "note": "Chỉ xóa dữ liệu local, không thao tác với server game"}


# Cho phép scan.py tái sử dụng SessionLocal
session_factory = SessionLocal
