"""Phân tích AI + lưu ai_analyses vào SQLite."""
import asyncio
from datetime import datetime
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AIAnalysis, Investigation, Scan, ScanResult
from app.schemas import AIAnalyzeRequest, AIAnalyzeResponse
from app.security import read_only as ro
from app.services.ai_service import AIOptions, analyze_with_provider, get_ai_status, repair_json
from app.services.metrics_service import calculate_metrics, classify
from app.services.nbt_service import parse_player_dat
from app.services.pika_service import pika_service

logger = logging.getLogger("pikamc.ai")
router = APIRouter(prefix="/api/ai", tags=["ai"])
settings = get_settings()

DEFAULT_MODELS = {
    "gemini": "gemini-1.5-flash",
    "openai": "gpt-4o-mini",
    "ollama": "qwen2.5:0.5b",
    "meta": "llama-3.3-70b-instruct",
    "llama": "llama-3.3-70b-instruct",
    "xai247": "deepseek-v4.1-flash",
}


async def build_realtime_forensics_context(req: AIAnalyzeRequest, db: Session) -> str:
    """Tổng hợp dữ liệu thế giới (world), nhật ký server (logs) và dữ liệu người chơi (user data & NBT) thời gian thực."""
    server_id = req.server_id
    if not server_id and req.scan_id:
        scan = db.get(Scan, req.scan_id)
        if scan:
            server_id = scan.server_id
    if not server_id:
        try:
            servers = await pika_service.get_servers()
            if servers and isinstance(servers, list):
                server_id = str(servers[0].get("id", ""))
        except Exception as e:
            logger.warning("Không lấy được danh sách server cho AI context: %s", e)

    if not server_id:
        return ""

    context_blocks: list[str] = [
        "\n\n======================================================================",
        "[DỮ LIỆU REAL-TIME FORENSICS HỆ THỐNG - GIÁM ĐỊNH CHUYÊN SÂU]",
        f"Server ID: {server_id} | Thời điểm cập nhật: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]

    # 1. World Data & Server Pulse
    if req.include_world_data:
        try:
            world, pulse = await asyncio.gather(
                pika_service.get_world_overview(server_id),
                pika_service.get_realtime_pulse(server_id),
                return_exceptions=True,
            )
            world_data = world if isinstance(world, dict) else {}
            pulse_data = pulse if isinstance(pulse, dict) else {}
            res = pulse_data.get("resources", {}) if isinstance(pulse_data, dict) else {}

            context_blocks.append("\n### 🌐 1. THẾ GIỚI & TÀI NGUYÊN MÁY CHỦ THỜI GIAN THỰC (WORLD DATA & PULSE)")
            context_blocks.append(f"- Tên thế giới (Level Name): {world_data.get('level_name', 'world')}")
            context_blocks.append(f"- Tuổi thọ thế giới: {world_data.get('world_age_days', 0)} ngày ({world_data.get('world_age_hours', 0)} giờ, {world_data.get('time_ticks', 0)} ticks)")
            context_blocks.append(f"- Chu kỳ thời gian: {world_data.get('time_of_day_phase', 'Không rõ')} | Chế độ: {world_data.get('game_type', 'Sinh tồn')} | Độ khó: {world_data.get('difficulty', 'Bình thường')}")
            context_blocks.append(f"- Tài nguyên máy chủ: CPU {res.get('cpu_percent', 0)}% | RAM {res.get('memory_mb', 0)}MB / {res.get('memory_limit_mb', 0)}MB ({res.get('memory_percent', 0)}%) | Disk {res.get('disk_mb', 0)}MB")
            context_blocks.append(f"- Số người chơi trực tuyến: {pulse_data.get('online_count', 0)} người (Báo cáo server: {pulse_data.get('server_reported_online', 0)})")
            context_blocks.append(f"- Thống kê file: {world_data.get('player_data_files', 0)} file playerdata, {world_data.get('player_stats_files', 0)} file stats")
        except Exception as e:
            logger.warning("Lỗi thu thập dữ liệu World cho AI: %s", e)

    # 2. Server Logs & Movement Violations
    logs_cache: list[dict[str, Any]] = []
    if req.include_logs:
        try:
            logs = await pika_service.get_logs(server_id, limit=req.log_limit, player_name=req.player_name)
            logs_cache = logs if isinstance(logs, list) else []
            context_blocks.append(f"\n### 📜 2. NHẬT KÝ SERVER & CẢNH BÁO VI PHẠM THỜI GIAN THỰC (LATEST LOGS - {len(logs_cache)} bản ghi)")

            movement_logs = [l for l in logs_cache if any(k in str(l.get("type", l.get("event", ""))).lower() for k in ("movement", "moved", "speed", "fly", "step"))]
            fastbreak_logs = [l for l in logs_cache if "fastbreak" in str(l.get("type", l.get("event", ""))).lower()]
            chat_logs = [l for l in logs_cache if "chat" in str(l.get("type", l.get("event", ""))).lower() or "command" in str(l.get("type", "")).lower()]

            context_blocks.append(f"- Tổng hợp sự kiện: {len(movement_logs)} cảnh báo di chuyển (Speed/Fly), {len(fastbreak_logs)} cảnh báo đào nhanh (FastBreak), {len(chat_logs)} bản ghi chat/lệnh")

            if logs_cache:
                context_blocks.append("- Chi tiết các mục log đáng chú ý gần nhất:")
                for log_item in logs_cache[:30]:
                    t = log_item.get("timestamp") or log_item.get("time", "")
                    player = log_item.get("player") or log_item.get("username", "")
                    msg = log_item.get("message") or log_item.get("raw") or log_item.get("description", "")
                    typ = log_item.get("type") or log_item.get("event", "log")
                    player_tag = f" [{player}]" if player else ""
                    context_blocks.append(f"  * [{t}] [{typ}]{player_tag}: {msg}")
            else:
                context_blocks.append("- (Không có cảnh báo vi phạm nào được ghi nhận trong snapshot log gần nhất)")
        except Exception as e:
            logger.warning("Lỗi thu thập Logs cho AI: %s", e)

    # 3. User Data & NBT Forensics
    if req.include_player_data:
        try:
            target_uuid = req.player_uuid
            target_name = req.player_name

            # Nếu có player_name mà chưa có UUID, tra cứu danh sách player
            if not target_uuid and target_name:
                players = await pika_service.get_player_list(server_id)
                for p in players:
                    if str(p.get("name", "")).lower() == target_name.lower():
                        target_uuid = p.get("uuid")
                        break

            # Nếu có UUID
            if target_uuid:
                stats = await pika_service.get_player_stats(server_id, target_uuid)
                raw_dat = await pika_service.download_file(server_id, target_uuid, "playerdata.dat")
                nbt = parse_player_dat(raw_dat) if raw_dat else {"ok": False, "inventory": [], "flags": {}}
                metrics = calculate_metrics(stats, nbt, logs_cache)
                verdict, confidence, evidence = classify(metrics)

                context_blocks.append("\n### 👤 3. DỮ LIỆU NGƯỜI CHƠI & TÚI ĐỒ NBT (USER DATA & INVENTORY)")
                context_blocks.append(f"- Đối tượng giám định: {target_name or 'N/A'} (UUID: {target_uuid})")
                context_blocks.append(f"- Đánh giá sơ bộ thuật toán: {verdict} (Độ tin cậy: {confidence}%)")
                context_blocks.append(f"- Tổng giờ chơi tích lũy: {metrics.play_hours}h | Tỷ lệ đào khoáng hiếm: {metrics.rare_ores_per_hour} ores/giờ (Kim cương, Netherite, Mảnh vụn cổ đại)")
                context_blocks.append(f"- Tốc độ di chuyển: {metrics.km_per_hour} km/h | Tỷ lệ K/D: {metrics.kdr} | Tốc độ phá khối: {metrics.blocks_per_hour} khối/giờ")
                context_blocks.append(f"- Quyền năng NBT (Abilities): mayfly={metrics.mayfly} (Khả năng bay), invulnerable={metrics.invulnerable} (Bất tử)")
                context_blocks.append(f"- Khoáng sản hiếm đang giữ trong người (NBT): {metrics.rare_ores_in_inventory} viên")

                if metrics.illegal_items:
                    context_blocks.append(f"- ⚠️ CẢNH BÁO VẬT PHẨM BẤT THƯỜNG / ILLEGAL ITEMS ({len(metrics.illegal_items)} món):")
                    for ill in metrics.illegal_items:
                        context_blocks.append(f"  * {ill}")
                else:
                    context_blocks.append("- Vật phẩm bất thường trong túi đồ: Không phát hiện")

                if evidence:
                    context_blocks.append("- Các điểm bằng chứng số học vi phạm:")
                    for ev in evidence:
                        context_blocks.append(f"  * [{ev.severity.upper()}] {ev.description} (Giá trị: {ev.value})")

                # Danh sách item nổi bật trong túi đồ (để AI phân tích)
                inventory = nbt.get("inventory", [])
                if inventory:
                    highlight_items = [
                        f"{it.get('count', 1)}x {it.get('id', '')}" +
                        (f" (Enchant: {', '.join([f'{e.get('id')}:{e.get('lvl')}' for e in it.get('ench', [])])})" if it.get("ench") else "")
                        for it in inventory if any(k in str(it.get("id", "")) for k in ("diamond", "netherite", "elytra", "totem", "golden_apple", "crystal", "shulker")) or it.get("ench")
                    ]
                    if highlight_items:
                        context_blocks.append(f"- Vật phẩm giá trị cao trong túi đồ: {', '.join(highlight_items[:15])}")
            else:
                # Nếu không chỉ định người chơi cụ thể: Liệt kê người chơi Online & Top offenders
                pulse_data = await pika_service.get_realtime_pulse(server_id)
                online_p = pulse_data.get("online_players", []) if isinstance(pulse_data, dict) else []
                context_blocks.append("\n### 👤 3. DANH SÁCH NGƯỜI CHƠI TRỰC TUYẾN & TỔNG QUAN")
                if online_p:
                    p_names = [p.get("name") or p.get("username", "") for p in online_p if isinstance(p, dict)]
                    context_blocks.append(f"- Danh sách người chơi online hiện tại ({len(p_names)}): {', '.join(p_names[:25])}")
                else:
                    context_blocks.append("- Không có danh sách người chơi online chi tiết trong pulse.")

                # Tra cứu top 5 đối tượng bị nghi vấn gần nhất trong database
                top_invs = db.query(Investigation).filter(Investigation.server_id == server_id).order_by(Investigation.created_at.desc()).limit(5).all()
                if top_invs:
                    context_blocks.append("- 5 hồ sơ điều tra gần nhất trên server:")
                    for inv in top_invs:
                        context_blocks.append(f"  * {inv.player_name} ({inv.verdict} - Tin cậy {inv.confidence}%): {inv.summary}")
        except Exception as e:
            logger.warning("Lỗi thu thập User Data cho AI: %s", e)

    context_blocks.append("======================================================================\n")
    return "\n".join(context_blocks)


@router.get("/status")
async def ai_status() -> dict[str, object]:
    """Kiểm tra trạng thái kết nối các mô hình AI (Gemini, OpenAI, Ollama)."""
    return get_ai_status()


@router.post("/analyze", response_model=AIAnalyzeResponse)
async def analyze(req: AIAnalyzeRequest, db: Session = Depends(get_db)) -> AIAnalyzeResponse:
    """Gọi AI provider — bắt buộc append READ_ONLY_SYSTEM_PROMPT khi strict_read_only=True."""
    t0 = time.perf_counter()

    # 1) Kiểm tra prompt nếu strict read-only
    if req.strict_read_only:
        is_safe, reason = ro.validate_prompt(req.prompt)
        if not is_safe:
            raise HTTPException(400, reason)

    # 2) Nạp dữ liệu Real-time Forensics (World, Logs, User/NBT Data)
    prompt_parts = [req.prompt]

    if req.include_world_data or req.include_logs or req.include_player_data or req.server_id:
        realtime_context = await build_realtime_forensics_context(req, db)
        if realtime_context:
            prompt_parts.append(realtime_context)

    # 3) Nếu có scan_id: load dữ liệu scan để build context payload
    if req.scan_id is not None:
        scan = db.get(Scan, req.scan_id)
        if scan is None:
            raise HTTPException(404, f"Không tìm thấy scan #{req.scan_id}")
        results = db.query(ScanResult).filter(ScanResult.scan_id == req.scan_id).all()
        payload = {
            "server_id": scan.server_id,
            "scan_status": scan.status,
            "summary": {"total": len(results),
                        "cheats": sum(1 for r in results if r.verdict == "CONFIRMED_CHEAT"),
                        "suspicious": sum(1 for r in results if r.verdict == "SUSPICIOUS")},
            "players": [{
                "name": r.player_name, "verdict": r.verdict, "confidence": r.confidence,
                "metrics": json.loads(r.metrics_json),
                "evidence": json.loads(r.evidence_json),
            } for r in results],
        }
        prompt_parts.append(
            "\n\n[DỮ LIỆU SCAN PHÂN TÍCH]\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        )

    full_prompt = "\n".join(prompt_parts)

    # 4) Build system prompt (READ-ONLY enforcement + Elite Forensic persona)
    forensic_persona = """
[STRICT READ-ONLY MODE - TRỢ LÝ AI GIÁM ĐỊNH CHUYÊN SÂU MINECRAFT]
Bạn là Trợ lý AI Giám định Chuyên sâu (Senior Minecraft Forensic Investigator).
Bạn được cấp quyền tiếp cận dữ liệu telemetry thời gian thực gồm:
- World Data: Tên thế giới, tuổi thọ thế giới (ticks/ngày), chu kỳ thời gian, độ khó, tải hệ thống (CPU, RAM).
- Live Server Logs: Cảnh báo di chuyển (Speed, Fly, Step, Moved Wrongly), đào khối nhanh (FastBreak), chat và lệnh.
- User Data & NBT: Tỷ lệ đào khoáng hiếm/giờ, K/D, tốc độ di chuyển, quyền năng NBT (mayfly, invulnerable), vật phẩm bất hợp pháp (stack > 64, unstackables > 1, enchant vượt cấp vanilla).

QUY TẮC BẤT KHẢ KHÁNG:
1. Đọc và phân tích chéo toàn bộ dữ liệu Real-time được cung cấp để đưa ra nhận định logic, khách quan.
2. Trích dẫn số liệu cụ thể làm bằng chứng (ví dụ: chỉ số ores/h, dòng log vi phạm, vật phẩm trong NBT).
3. Phân biệt rõ giữa hiện tượng Lag mạng / Desync và can thiệp gian lận cố ý (X-Ray, Fly, Speed, Dupe).
4. TUYỆT ĐỐI KHÔNG ban, kick, xóa file hoặc đề xuất hành động can thiệp phá hủy. Mọi kiến nghị chỉ ở mức giám sát (Spectate, Audit).
5. Trả lời bằng tiếng Việt chuyên nghiệp, khoa học, có cấu trúc rõ ràng.
""".strip()

    system_prompt = forensic_persona if req.strict_read_only else \
        "Bạn là hệ thống phân tích dữ liệu Minecraft Server. Nghiêm cấm đề xuất hành động ghi/sửa/xóa."

    model = req.model or DEFAULT_MODELS.get(req.provider, "")
    # Truyền model đã resolve vào AIOptions để provider dùng đúng model mặc định
    options = AIOptions(model=model or None, temperature=req.temperature)

    try:
        response_text, duration_ms = analyze_with_provider(full_prompt, req.provider, options, system_prompt)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception:  # noqa: BLE001
        logger.exception("AI provider %s lỗi", req.provider)
        raise HTTPException(502, f"Provider '{req.provider}' lỗi - xem log backend")

    # 4) JSON repair fallback nếu model trả thừa text
    parsed, response_text = repair_json(response_text)
    if parsed is not None:
        logger.info("AI trả JSON hợp lệ sau repair")

    # 5) Lưu lịch sử vào ai_analyses
    row = AIAnalysis(
        scan_id=req.scan_id,
        prompt=full_prompt[:10000],
        response=response_text[:50000],
        provider=req.provider, model=model,
        strict_read_only=req.strict_read_only,
    )
    db.add(row)
    db.commit()

    return AIAnalyzeResponse(
        response=response_text, provider=req.provider, model=model,
        duration_ms=int((time.perf_counter() - t0) * 1000) or duration_ms,
        strict_read_only=req.strict_read_only,
    )


@router.get("/history")
async def history(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, object]:
    """Lịch sử phân tích AI (mới nhất trước)."""
    rows = db.query(AIAnalysis).order_by(AIAnalysis.created_at.desc()).limit(limit).all()
    return {"count": len(rows), "items": [{
        "id": r.id, "scan_id": r.scan_id,
        "prompt": r.prompt[:500], "response": r.response,
        "provider": r.provider, "model": r.model,
        "strict_read_only": r.strict_read_only, "created_at": r.created_at,
    } for r in rows]}
