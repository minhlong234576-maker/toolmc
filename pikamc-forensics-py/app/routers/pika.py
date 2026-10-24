import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.pika_service import pika_service

logger = logging.getLogger("pikamc.pika")
router = APIRouter(prefix="/api/servers", tags=["pika"])


class PowerActionRequest(BaseModel):
    action: Literal["start", "stop", "restart", "kill"] = Field(
        ..., description="Hành động nguồn: start (mở), stop (tắt), restart (khởi động lại), kill (dừng khẩn cấp)"
    )


class ConsoleCommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=1000, description="Lệnh thực thi trong console server")


class CreateScheduleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Tên lịch trình")
    cron: str = Field(..., min_length=5, max_length=50, description="Biểu thức cron 5 phần (VD: '0 4 * * *')")
    action_type: Literal["restart", "start", "stop", "kill", "command"] = Field("restart", description="Loại hành động")
    command: str | None = Field(None, description="Lệnh console nếu action_type là 'command'")
    enabled: bool = Field(True, description="Kích hoạt ngay sau khi tạo")


class ToggleScheduleRequest(BaseModel):
    enabled: bool = Field(..., description="Trạng thái bật/tắt lịch trình")


@router.get("")
async def get_servers_list() -> dict[str, object]:
    """Danh sách các server Minecraft thực tế từ PikaMC API."""
    servers = await pika_service.get_servers()
    return {"count": len(servers), "servers": servers}


@router.get("/{server_id}/overview")
async def get_server_overview(server_id: str) -> dict[str, object]:
    """Lấy toàn bộ thông tin chi tiết cấu hình, tài nguyên và quản lý của server."""
    overview = await pika_service.get_server_overview(server_id)
    return overview


@router.get("/{server_id}/logs")
async def get_server_logs(
    server_id: str,
    limit: int = Query(100, ge=1, le=1000),
    player_name: str | None = Query(None),
) -> dict[str, object]:
    """Logs server (movement/fastbreak...) - cache 60s."""
    logs = await pika_service.get_logs(server_id, limit=limit, player_name=player_name)
    return {"server_id": server_id, "count": len(logs), "logs": logs}


@router.get("/{server_id}/players/{uuid}/stats")
async def get_player_stats(server_id: str, uuid: str) -> dict[str, object]:
    """Thống kê đời chơi player - cache 5 phút."""
    data = await pika_service.get_player_stats(server_id, uuid)
    if not data:
        raise HTTPException(404, f"Không lấy được stats cho player {uuid}")
    return data


@router.get("/{server_id}/players/{uuid}/data")
async def get_player_data(server_id: str, uuid: str) -> dict[str, object]:
    """Danh sách file data (.dat) của player."""
    data = await pika_service.get_player_data(server_id, uuid)
    if not data:
        raise HTTPException(404, f"Không lấy được data cho player {uuid}")
    return data


@router.get("/{server_id}/players/{uuid}/forensics")
async def get_player_forensics(server_id: str, uuid: str) -> dict[str, object]:
    """Dữ liệu forensics tổng hợp từ PikaMC."""
    data = await pika_service.get_player_forensics(server_id, uuid)
    if not data:
        raise HTTPException(404, f"Không lấy được forensics cho player {uuid}")
    return data


@router.get("/{server_id}/players")
async def get_player_list(
    server_id: str,
    force_refresh: bool = Query(False),
) -> dict[str, object]:
    """Danh sách player của server (hỗ trợ force_refresh realtime)."""
    players = await pika_service.get_player_list(server_id, force_refresh=force_refresh)
    return {"server_id": server_id, "count": len(players), "players": players}


@router.get("/{server_id}/live-activity")
async def get_server_live_activity(
    server_id: str,
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, object]:
    """Dòng sự kiện kết nối người chơi Real-Time (vào/ra server, IP, thời gian)."""
    activity = await pika_service.get_live_activity(server_id, limit=limit)
    return activity


@router.get("/{server_id}/realtime-pulse")
async def get_server_realtime_pulse(server_id: str) -> dict[str, object]:
    """Gói cập nhật nhịp đập Real-Time toàn diện: Tài nguyên, Người chơi Online, Sự kiện, World data."""
    pulse = await pika_service.get_realtime_pulse(server_id)
    return pulse


@router.get("/{server_id}/world-overview")
async def get_server_world_overview(server_id: str) -> dict[str, object]:
    """Dữ liệu chi tiết Thế Giới (world data): level.dat, thời gian in-game, số file player, phiên bản."""
    world = await pika_service.get_world_overview(server_id)
    return world


@router.get("/{server_id}/logs/files")
async def get_server_log_files(server_id: str) -> dict[str, object]:
    """Danh sách toàn bộ các file log trên server (latest.log và kho lưu trữ .log.gz toàn thời gian)."""
    files = await pika_service.get_log_files(server_id)
    return {"server_id": server_id, "count": len(files), "files": files}


@router.get("/{server_id}/logs/view")
async def view_server_log_file(
    server_id: str,
    file: str = Query("latest.log"),
    limit: int = Query(300, ge=1, le=2000),
    category: str | None = Query(None),
    search: str | None = Query(None),
) -> dict[str, object]:
    """Xem và phân tích bất kỳ file log nào (hỗ trợ lọc cảnh báo, vi phạm cheat, tìm kiếm)."""
    result = await pika_service.read_log_file(
        server_id=server_id,
        filename=file,
        limit=limit,
        category=category,
        search=search,
    )
    return result


# ---------- Server Power Control ----------

@router.post("/{server_id}/power")
async def send_server_power_action(
    server_id: str,
    payload: PowerActionRequest,
) -> dict[str, object]:
    """Gửi lệnh nguồn (start, stop, restart, kill) tới server."""
    try:
        res = await pika_service.send_power_action(server_id, payload.action)
        if not res.get("success"):
            raise HTTPException(
                status_code=res.get("status_code", 400),
                detail=res.get("error", "Lỗi gửi lệnh nguồn"),
            )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{server_id}/command")
async def send_server_console_command(
    server_id: str,
    payload: ConsoleCommandRequest,
) -> dict[str, object]:
    """Gửi lệnh trực tiếp vào Console server."""
    try:
        res = await pika_service.send_console_command(server_id, payload.command)
        if not res.get("success"):
            raise HTTPException(
                status_code=res.get("status_code", 400),
                detail=res.get("error", "Lỗi gửi lệnh console"),
            )
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- Scheduled Tasks (Lịch Trình Tự Động) ----------

@router.get("/{server_id}/schedules")
async def get_server_schedules(server_id: str) -> dict[str, object]:
    """Lấy danh sách các lịch trình tự động (Schedules) của máy chủ."""
    schedules = await pika_service.get_schedules(server_id)
    return {"server_id": server_id, "count": len(schedules), "schedules": schedules}


@router.post("/{server_id}/schedules")
async def create_server_schedule(
    server_id: str,
    payload: CreateScheduleRequest,
) -> dict[str, object]:
    """Tạo lịch trình tự động mới (ví dụ restart lúc 04:00 AM, backup, lệnh console)."""
    try:
        res = await pika_service.create_schedule(
            server_id=server_id,
            name=payload.name,
            cron_expr=payload.cron,
            action_type=payload.action_type,
            command=payload.command,
            enabled=payload.enabled,
        )
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("error", "Lỗi tạo lịch trình"))
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{server_id}/schedules/{schedule_id}/trigger")
async def trigger_server_schedule(
    server_id: str,
    schedule_id: str,
) -> dict[str, object]:
    """Kích hoạt lịch trình chạy ngay lập tức mà không cần đợi đến giờ."""
    res = await pika_service.trigger_schedule(server_id, schedule_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Không thể kích hoạt lịch trình"))
    return res


@router.patch("/{server_id}/schedules/{schedule_id}/toggle")
async def toggle_server_schedule(
    server_id: str,
    schedule_id: str,
    payload: ToggleScheduleRequest,
) -> dict[str, object]:
    """Bật hoặc tắt trạng thái kích hoạt của lịch trình."""
    res = await pika_service.toggle_schedule(server_id, schedule_id, payload.enabled)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Không thể cập nhật trạng thái lịch trình"))
    return res


@router.delete("/{server_id}/schedules/{schedule_id}")
async def delete_server_schedule(
    server_id: str,
    schedule_id: str,
) -> dict[str, object]:
    """Xóa hoàn toàn một lịch trình."""
    res = await pika_service.delete_schedule(server_id, schedule_id)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Không thể xóa lịch trình"))
    return res

