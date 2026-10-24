"""Quét world chế background task + lưu SQLite."""
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Scan, ScanResult
from app.routers.investigate import run_investigation
from app.schemas import ScanCreatedResponse, ScanStatusResponse, ScanWorldRequest
from app.services.pika_service import pika_service

logger = logging.getLogger("pikamc.scan")
router = APIRouter(prefix="/api/scan", tags=["scan"])


ACTIVE_SCAN_STATES: dict[int, dict[str, object]] = {}
CANCEL_REQUESTS: set[int] = set()


async def _run_scan_background(scan_id: int, server_id: str) -> None:
    """Background task async: quét từng player, cập nhật progress + kết quả vào SQLite."""
    from app.database import SessionLocal
    db = SessionLocal()
    ACTIVE_SCAN_STATES[scan_id] = {"current_player": "Đang chuẩn bị danh sách...", "total": 0}
    try:
        players = await pika_service.get_player_list(server_id)
        scan = db.get(Scan, scan_id)
        if scan is None:
            return
        scan.total_players = len(players)
        db.commit()
        ACTIVE_SCAN_STATES[scan_id]["total"] = len(players)

        for i, p in enumerate(players):
            if scan_id in CANCEL_REQUESTS:
                logger.info("Scan #%d bị hủy bởi người dùng.", scan_id)
                current = db.get(Scan, scan_id)
                if current is not None:
                    current.status = "stopped"
                    current.completed_at = func.now()
                    db.commit()
                break

            pname = str(p.get("name", p.get("player_name", "unknown")))
            puuid = str(p.get("uuid", p.get("player_uuid", "")))
            if not puuid:
                continue

            ACTIVE_SCAN_STATES[scan_id]["current_player"] = pname

            # Mỗi player có session riêng để tránh transaction conflicts
            item_db = SessionLocal()
            try:
                result = await run_investigation(server_id, pname, puuid, db=None)
                item_db.add(ScanResult(
                    scan_id=scan_id, player_name=pname, player_uuid=puuid,
                    verdict=str(result["verdict"]),
                    confidence=int(result["confidence"]),
                    metrics_json=json.dumps(result["metrics"]),
                    evidence_json=json.dumps(result["evidence"]),
                ))
                item_db.commit()
                # Cập nhật counters + progress
                current = db.get(Scan, scan_id)
                if current is not None:
                    current.scanned_players = i + 1
                    current.progress = int((i + 1) * 100 / max(len(players), 1))
                    if result["verdict"] == "CONFIRMED_CHEAT":
                        current.cheat_count += 1
                    elif result["verdict"] == "SUSPICIOUS":
                        current.suspicious_count += 1
                    else:
                        current.clean_count += 1
                    db.commit()
            except Exception as e:  # noqa: BLE001 — 1 player lỗi không dừng scan
                logger.error("Scan player %s/%s lỗi: %s", pname, puuid, e)
                item_db.rollback()
                db.rollback()
            finally:
                item_db.close()

        scan = db.get(Scan, scan_id)
        if scan is not None and scan.status != "stopped":
            scan.status = "completed"
            scan.progress = 100
            scan.completed_at = func.now()
            db.commit()
            logger.info("Scan #%d hoàn tất (%d players)", scan_id, len(players))
    except Exception:  # noqa: BLE001
        logger.exception("Scan #%d thất bại - đặt status=failed", scan_id)
        try:
            db.rollback()
            scan = db.get(Scan, scan_id)
            if scan is not None:
                scan.status = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        ACTIVE_SCAN_STATES.pop(scan_id, None)
        CANCEL_REQUESTS.discard(scan_id)
        db.close()


@router.post("/world", response_model=ScanCreatedResponse)
async def scan_world(req: ScanWorldRequest, background: BackgroundTasks,
                     db: Session = Depends(get_db)) -> ScanCreatedResponse:
    """Bắt đầu scan world — tạo bản ghi + chạy background task."""
    server_id = req.server_id.strip()
    if not server_id:
        raise HTTPException(422, "Thiếu trường 'server_id'")

    scan = Scan(server_id=server_id, status="processing")
    db.add(scan)
    db.commit()
    db.refresh(scan)

    background.add_task(_run_scan_background, scan.id, server_id)
    logger.info("Khởi động scan #%d cho server %s", scan.id, server_id)
    return ScanCreatedResponse(scan_id=scan.id, status=scan.status)


@router.post("/{scan_id}/stop")
async def stop_scan(scan_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    """Dừng tiến trình quét đang chạy."""
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, f"Không tìm thấy scan #{scan_id}")
    if scan.status != "processing":
        return {"scan_id": scan_id, "status": scan.status, "message": "Scan không ở trạng thái đang chạy"}
    CANCEL_REQUESTS.add(scan_id)
    return {"scan_id": scan_id, "status": "stopping", "message": "Đã gửi tín hiệu dừng quét"}


@router.get("/history")
async def scan_history(limit: int = Query(20, ge=1, le=100),
                       db: Session = Depends(get_db)) -> dict[str, object]:
    """Danh sách các lần scan (mới nhất trước)."""
    rows = db.query(Scan).order_by(Scan.created_at.desc()).limit(limit).all()
    return {"count": len(rows), "items": [_scan_to_dict(r) for r in rows]}


@router.get("/{scan_id}/status", response_model=ScanStatusResponse)
async def scan_status(scan_id: int, db: Session = Depends(get_db)) -> ScanStatusResponse:
    """Progress + counters hiện tại của scan."""
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, f"Không tìm thấy scan #{scan_id}")
    curr_state = ACTIVE_SCAN_STATES.get(scan_id, {})
    curr_player = str(curr_state.get("current_player")) if scan.status == "processing" and curr_state.get("current_player") else None
    return ScanStatusResponse(
        id=scan.id, server_id=scan.server_id, status=scan.status, progress=scan.progress,
        total_players=scan.total_players, scanned_players=scan.scanned_players,
        cheat_count=scan.cheat_count, suspicious_count=scan.suspicious_count,
        clean_count=scan.clean_count, current_player=curr_player,
        created_at=scan.created_at, completed_at=scan.completed_at,
    )


@router.get("/{scan_id}/results")
async def scan_results(scan_id: int, verdict: str | None = Query(None),
                       sort: str = Query("confidence", pattern="^(confidence|created_at|id)$"),
                       db: Session = Depends(get_db)) -> dict[str, object]:
    """Kết quả chi tiết, filter + sort bằng SQL."""
    q = db.query(ScanResult).filter(ScanResult.scan_id == scan_id)
    if verdict:
        q = q.filter(ScanResult.verdict == verdict.upper())
    col = getattr(ScanResult, sort, ScanResult.confidence)
    rows = q.order_by(col.desc()).all()
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, f"Không tìm thấy scan #{scan_id}")
    return {
        "scan_id": scan_id, "count": len(rows),
        "items": [{
            "id": r.id, "scan_id": r.scan_id, "player_name": r.player_name,
            "player_uuid": r.player_uuid, "verdict": r.verdict, "confidence": r.confidence,
            "metrics": json.loads(r.metrics_json), "evidence": json.loads(r.evidence_json),
            "created_at": r.created_at,
        } for r in rows],
    }


def _scan_to_dict(scan: Scan) -> dict[str, object]:
    """Serialize Scan row thành dict (dùng chung)."""
    return {
        "id": scan.id, "server_id": scan.server_id, "status": scan.status,
        "progress": scan.progress, "total_players": scan.total_players,
        "scanned_players": scan.scanned_players, "cheat_count": scan.cheat_count,
        "suspicious_count": scan.suspicious_count, "clean_count": scan.clean_count,
        "created_at": scan.created_at, "completed_at": scan.completed_at,
    }
