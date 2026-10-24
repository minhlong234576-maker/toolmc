import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import get_settings
from app.services.cache_service import cache_get, cache_set
from app.services.nbt_service import parse_dat_buffer, _to_plain

logger = logging.getLogger("pikamc.pika_service")

settings = get_settings()

MAX_RETRIES = 3


class PikaService:
    """Async client kết nối tới PikaMC API (giám sát, điều khiển nguồn máy chủ, quản lý lịch trình)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_base_url(self) -> str:
        url = settings.PIKAMC_API_URL.strip().rstrip("/")
        if url.endswith("/api"):
            url = url[:-4]
        return url

    def _get_client(self) -> httpx.AsyncClient:
        """Tạo (hoặc tái sử dụng) AsyncClient với Authorization header."""
        if self._client is None or self._client.is_closed:
            base_url = self._get_base_url()
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Authorization": f"Bearer {settings.PIKAMC_API_KEY}",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=httpx.Timeout(15.0),
            )
        return self._client

    async def close(self) -> None:
        """Đóng client lúc app shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None,
                   ttl: int = 60, is_json: bool = True) -> Any:
        """GET + retry exponential backoff + TTL cache."""
        # Cache key ổn định: sort params để cùng query -> cùng key bất kể thứ tự
        params_key = json.dumps(params, sort_keys=True, default=str) if params else ""
        cache_key = f"pika:GET:{path}:{params_key}"
        cached = cache_get(cache_key)
        if cached is not None:
            return cached

        client = self._get_client()
        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.get(path, params=params)
                resp.raise_for_status()
                if is_json:
                    data = resp.json()
                else:
                    data = resp.content
                cache_set(cache_key, data, ttl)
                return data
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500 and e.response.status_code != 429:
                    logger.warning("PikaMC API %s -> HTTP %d", path, e.response.status_code)
                    return None
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("PikaMC GET %s lỗi (lần %d/%d): %s", path, attempt, MAX_RETRIES, e)
            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
        logger.error("PikaMC GET %s thất bại sau %d lần: %s", path, MAX_RETRIES, last_err)
        return None

    async def _post(self, path: str, json_data: dict[str, Any] | None = None) -> tuple[int, Any]:
        """POST request tới PikaMC API (không cache). Trả về (status_code, data/error)."""
        client = self._get_client()
        try:
            resp = await client.post(path, json=json_data)
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
            else:
                data = {}
            return resp.status_code, data
        except Exception as e:
            logger.error("PikaMC POST %s lỗi: %s", path, e)
            return 500, {"errors": [str(e)]}

    async def _delete(self, path: str) -> tuple[int, Any]:
        """DELETE request tới PikaMC API."""
        client = self._get_client()
        try:
            resp = await client.delete(path)
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
            else:
                data = {}
            return resp.status_code, data
        except Exception as e:
            logger.error("PikaMC DELETE %s lỗi: %s", path, e)
            return 500, {"errors": [str(e)]}

    async def _patch(self, path: str, json_data: dict[str, Any] | None = None) -> tuple[int, Any]:
        """PATCH request tới PikaMC API."""
        client = self._get_client()
        try:
            resp = await client.patch(path, json=json_data)
            if resp.content:
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
            else:
                data = {}
            return resp.status_code, data
        except Exception as e:
            logger.error("PikaMC PATCH %s lỗi: %s", path, e)
            return 500, {"errors": [str(e)]}

    # ---------- Server Control & Schedules ----------

    async def send_power_action(self, server_id: str, action: str) -> dict[str, Any]:
        """Gửi lệnh nguồn (start, stop, restart, kill) tới server."""
        valid_actions = {"start", "stop", "restart", "kill"}
        action_clean = action.strip().lower()
        if action_clean not in valid_actions:
            raise ValueError(f"Hành động nguồn không hợp lệ: '{action}'. Cho phép: {list(valid_actions)}")

        status_code, data = await self._post(f"/api/client/servers/{server_id}/power", json_data={"action": action_clean})
        if status_code in (200, 204):
            return {"success": True, "server_id": server_id, "action": action_clean, "message": f"Đã gửi tín hiệu {action_clean.upper()} tới máy chủ thành công."}

        err_msg = data.get("errors", [str(data)]) if isinstance(data, dict) else str(data)
        logger.error("Gửi lệnh nguồn %s tới server %s thất bại (%d): %s", action_clean, server_id, status_code, err_msg)
        return {"success": False, "server_id": server_id, "action": action_clean, "status_code": status_code, "error": err_msg}

    async def send_console_command(self, server_id: str, command: str) -> dict[str, Any]:
        """Gửi lệnh trực tiếp vào Console máy chủ."""
        cmd_clean = command.strip()
        if not cmd_clean:
            raise ValueError("Lệnh console không được để trống.")

        # Xóa dấu '/' ở đầu nếu có vì console thường không cần gạch chéo
        if cmd_clean.startswith("/"):
            cmd_clean = cmd_clean[1:].strip()

        status_code, data = await self._post(f"/api/client/servers/{server_id}/command", json_data={"command": cmd_clean})
        if status_code in (200, 204):
            return {"success": True, "server_id": server_id, "command": cmd_clean, "message": f"Đã thực thi lệnh console thành công: /{cmd_clean}"}

        err_msg = data.get("errors", [str(data)]) if isinstance(data, dict) else str(data)
        logger.error("Gửi lệnh console tới server %s thất bại (%d): %s", server_id, status_code, err_msg)
        return {"success": False, "server_id": server_id, "command": cmd_clean, "status_code": status_code, "error": err_msg}

    def _describe_cron(self, expr: str) -> str:
        """Chuyển biểu thức cron 5 phần thành mô tả tiếng Việt dễ hiểu."""
        parts = expr.strip().split()
        if len(parts) != 5:
            return f"Cron: {expr}"
        minute, hour, dom, mon, dow = parts
        if minute == "0" and hour == "4" and dom == "*" and mon == "*" and dow == "*":
            return "Mỗi ngày lúc 04:00 AM (Sáng)"
        if minute == "0" and hour == "0" and dom == "*" and mon == "*" and dow == "*":
            return "Mỗi ngày lúc nửa đêm (00:00 AM)"
        if minute == "0" and hour == "12" and dom == "*" and mon == "*" and dow == "*":
            return "Mỗi ngày lúc 12:00 PM (Trưa)"
        if minute == "0" and hour == "*/6":
            return "Mỗi 6 tiếng một lần"
        if minute == "0" and hour == "*/12":
            return "Mỗi 12 tiếng một lần"
        if minute == "0" and hour == "*/4":
            return "Mỗi 4 tiếng một lần"
        if minute == "0" and hour == "*/2":
            return "Mỗi 2 tiếng một lần"
        if minute == "0" and hour == "*":
            return "Mỗi tiếng một lần (đầu giờ)"
        if minute.isdigit() and hour.isdigit() and dom == "*" and mon == "*" and dow == "*":
            return f"Hàng ngày lúc {int(hour):02d}:{int(minute):02d}"
        return f"Cron: {expr}"

    async def get_schedules(self, server_id: str) -> list[dict[str, Any]]:
        """Lấy danh sách các lịch trình tự động (Schedules) và chi tiết các bước (steps)."""
        data = await self._get(f"/api/client/servers/{server_id}/schedules", ttl=2)
        if not data or not isinstance(data, dict):
            return []

        raw_list = data.get("schedules", {}).get("data", []) or data.get("data", [])
        schedules: list[dict[str, Any]] = []

        for item in raw_list:
            attrs = item.get("attributes", item)
            s_uuid = attrs.get("uuid") or item.get("uuid")
            name = attrs.get("name") or item.get("name") or "Lịch trình"
            enabled = attrs.get("enabled", True)
            triggers = attrs.get("triggers", [])
            last_run = attrs.get("last_run")
            last_failure = attrs.get("last_failure")
            created = attrs.get("created")

            # Trích xuất biểu thức cron
            cron_expr = "* * * * *"
            cron_desc = "Không xác định"
            for tr in triggers:
                if tr.get("type") == "cron":
                    cron_expr = tr.get("schedule", "* * * * *")
                    cron_desc = self._describe_cron(cron_expr)
                    break

            # Lấy các bước thực thi (steps) của schedule
            steps_data = await self._get(f"/api/client/servers/{server_id}/schedules/{s_uuid}/steps", ttl=2)
            steps_list: list[dict[str, Any]] = []
            if steps_data and isinstance(steps_data, dict):
                steps_val = steps_data.get("schedule_steps")
                if isinstance(steps_val, list):
                    raw_steps = steps_val
                elif isinstance(steps_val, dict):
                    raw_steps = steps_val.get("data", [])
                else:
                    raw_steps = steps_data.get("data", []) if isinstance(steps_data.get("data"), list) else []

                for st in raw_steps:
                    st_attrs = st.get("attributes", st)
                    act = st_attrs.get("action", {})
                    steps_list.append({
                        "uuid": st_attrs.get("uuid"),
                        "order": st_attrs.get("order", 0),
                        "type": act.get("type", "unknown"),
                        "action": act.get("action", ""),
                        "command": act.get("command", ""),
                    })

            schedules.append({
                "uuid": str(s_uuid),
                "name": str(name),
                "enabled": bool(enabled),
                "cron_expr": cron_expr,
                "cron_desc": cron_desc,
                "triggers": triggers,
                "steps": steps_list,
                "last_run": last_run,
                "last_failure": last_failure,
                "created": created,
            })

        return schedules

    async def create_schedule(self, server_id: str, name: str, cron_expr: str,
                              action_type: str = "restart", command: str | None = None,
                              enabled: bool = True) -> dict[str, Any]:
        """Tạo lịch trình tự động (ví dụ restart định kỳ) và gán step hành động tương ứng."""
        clean_cron = cron_expr.strip()
        parts = clean_cron.split()
        if len(parts) != 5:
            raise ValueError(f"Biểu thức Cron không hợp lệ (phải có 5 phần, ví dụ '0 4 * * *'): '{cron_expr}'")

        # 1. Tạo Schedule
        payload = {
            "name": name.strip(),
            "enabled": enabled,
            "triggers": [{"type": "cron", "schedule": clean_cron}],
            "condition": {"type": "none"}
        }
        status_code, data = await self._post(f"/api/client/servers/{server_id}/schedules", json_data=payload)
        if status_code not in (200, 201) or not isinstance(data, dict):
            err_msg = data.get("errors", [str(data)]) if isinstance(data, dict) else str(data)
            return {"success": False, "error": f"Không thể tạo lịch trình: {err_msg}"}

        sched = data.get("schedule", data.get("attributes", data))
        s_uuid = sched.get("uuid")
        if not s_uuid:
            return {"success": False, "error": "API không trả về UUID của lịch trình vừa tạo"}

        # 2. Tạo Step thực thi
        step_action: dict[str, Any]
        if action_type in ("restart", "start", "stop", "kill"):
            step_action = {
                "type": "send_power",
                "ignore_failure": False,
                "action": action_type
            }
        elif action_type == "command" and command:
            cmd = command.strip()
            if cmd.startswith("/"):
                cmd = cmd[1:].strip()
            step_action = {
                "type": "send_command",
                "ignore_failure": False,
                "command": cmd
            }
        else:
            step_action = {
                "type": "send_power",
                "ignore_failure": False,
                "action": "restart"
            }

        step_code, step_data = await self._post(
            f"/api/client/servers/{server_id}/schedules/{s_uuid}/steps",
            json_data={"order": 0, "action": step_action}
        )
        if step_code not in (200, 201):
            logger.warning("Tạo step cho schedule %s trả về %d: %s", s_uuid, step_code, step_data)

        return {
            "success": True,
            "uuid": s_uuid,
            "name": name,
            "cron_expr": clean_cron,
            "cron_desc": self._describe_cron(clean_cron),
            "action_type": action_type,
            "message": f"Đã tạo thành công lịch trình '{name}' ({clean_cron})"
        }

    async def trigger_schedule(self, server_id: str, schedule_id: str) -> dict[str, Any]:
        """Kích hoạt lịch trình chạy ngay lập tức."""
        status_code, data = await self._post(f"/api/client/servers/{server_id}/schedules/{schedule_id}/trigger", json_data={"skip_condition": True})
        if status_code in (200, 204):
            return {"success": True, "schedule_id": schedule_id, "message": "Đã kích hoạt chạy lịch trình thành công!"}
        err_msg = data.get("errors", [str(data)]) if isinstance(data, dict) else str(data)
        return {"success": False, "schedule_id": schedule_id, "error": err_msg}

    async def toggle_schedule(self, server_id: str, schedule_id: str, enabled: bool) -> dict[str, Any]:
        """Bật hoặc tắt kích hoạt một lịch trình."""
        status_code, data = await self._patch(f"/api/client/servers/{server_id}/schedules/{schedule_id}", json_data={"enabled": enabled})
        if status_code in (200, 204):
            status_text = "Bật" if enabled else "Tắt"
            return {"success": True, "schedule_id": schedule_id, "enabled": enabled, "message": f"Đã {status_text} lịch trình thành công."}
        err_msg = data.get("errors", [str(data)]) if isinstance(data, dict) else str(data)
        return {"success": False, "schedule_id": schedule_id, "error": err_msg}

    async def delete_schedule(self, server_id: str, schedule_id: str) -> dict[str, Any]:
        """Xóa lịch trình."""
        status_code, data = await self._delete(f"/api/client/servers/{server_id}/schedules/{schedule_id}")
        if status_code in (200, 204):
            return {"success": True, "schedule_id": schedule_id, "message": "Đã xóa lịch trình thành công."}
        err_msg = data.get("errors", [str(data)]) if isinstance(data, dict) else str(data)
        return {"success": False, "schedule_id": schedule_id, "error": err_msg}

    # ---------- Public API (chỉ GET) ----------

    async def get_servers(self, ttl: int = 180) -> list[dict[str, Any]]:
        """Lấy danh sách các Server Minecraft thực tế trên tài khoản PikaMC."""
        # 1. Thử Pterodactyl-based client API
        data = await self._get("/api/client/servers", ttl=ttl)
        if data and isinstance(data, dict):
            raw_list = data.get("servers", {}).get("data") or data.get("data") or []
            servers = []
            for item in raw_list:
                attrs = item.get("attributes", item)
                short_id = attrs.get("uuid_short") or attrs.get("identifier") or attrs.get("id") or item.get("uuid_short")
                uuid = attrs.get("uuid") or item.get("uuid")
                name = attrs.get("name") or item.get("name") or "Minecraft Server"
                status = attrs.get("status") or item.get("status") or "online"
                node = attrs.get("node") or item.get("node")
                servers.append({
                    "id": str(short_id),
                    "uuid": str(uuid or short_id),
                    "name": str(name),
                    "status": str(status),
                    "node": str(node or "default"),
                })
            if servers:
                return servers

        # 2. Fallback API hub cũ nếu có
        old_data = await self._get("/servers", ttl=ttl)
        if old_data and isinstance(old_data, list) and len(old_data) > 0:
            return old_data
        if old_data and isinstance(old_data, dict):
            servers = old_data.get("servers", [])
            if servers:
                return servers

        # 3. Fallback server mặc định nếu API không trả về danh sách
        return [{
            "id": "3fe38490",
            "uuid": "3fe38490-0446-49be-a45a-0ecd69c0cac9",
            "name": "hosting-Bảo Gia",
            "status": "online",
            "node": "default",
        }]

    async def get_player_list(self, server_id: str, ttl: int = 60, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Lấy danh sách TOÀN BỘ player của server (từ usercache.json, ops.json, banned-players.json và world files)."""
        if force_refresh:
            ttl = 0
        players_map: dict[str, dict[str, Any]] = {}

        # 1. Đọc usercache.json (chứa tên hiển thị chuẩn và UUID của toàn bộ người chơi)
        cache_content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "usercache.json"}, ttl=ttl, is_json=False)
        if cache_content:
            try:
                text = cache_content.decode("utf-8", errors="ignore") if isinstance(cache_content, bytes) else cache_content
                cache_list = json.loads(text)
                for item in cache_list:
                    uuid = item.get("uuid")
                    name = item.get("name")
                    if uuid:
                        players_map[uuid] = {
                            "name": name or uuid[:8],
                            "uuid": uuid,
                            "status": "offline",
                            "avatar": f"https://mc-heads.net/avatar/{uuid}/40",
                            "role": "player",
                            "expires_on": item.get("expiresOn", ""),
                        }
            except Exception as e:
                logger.warning("Parse usercache.json lỗi: %s", e)

        # 2. Đọc ops.json (Admin / Staff)
        ops_content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "ops.json"}, ttl=ttl, is_json=False)
        if ops_content:
            try:
                text = ops_content.decode("utf-8", errors="ignore") if isinstance(ops_content, bytes) else ops_content
                ops_list = json.loads(text)
                for o in ops_list:
                    uuid = o.get("uuid")
                    if uuid:
                        if uuid in players_map:
                            players_map[uuid]["role"] = "admin"
                        else:
                            players_map[uuid] = {
                                "name": o.get("name", uuid[:8]),
                                "uuid": uuid,
                                "status": "offline",
                                "avatar": f"https://mc-heads.net/avatar/{uuid}/40",
                                "role": "admin",
                            }
            except Exception as e:
                logger.warning("Parse ops.json lỗi: %s", e)

        # 3. Đọc banned-players.json (Người chơi bị cấm)
        ban_content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "banned-players.json"}, ttl=ttl, is_json=False)
        if ban_content:
            try:
                text = ban_content.decode("utf-8", errors="ignore") if isinstance(ban_content, bytes) else ban_content
                ban_list = json.loads(text)
                for b in ban_list:
                    uuid = b.get("uuid")
                    if uuid:
                        reason = b.get("reason", "Bị cấm bởi Server")
                        if uuid in players_map:
                            players_map[uuid]["status"] = "banned"
                            players_map[uuid]["ban_reason"] = reason
                        else:
                            players_map[uuid] = {
                                "name": b.get("name", uuid[:8]),
                                "uuid": uuid,
                                "status": "banned",
                                "avatar": f"https://mc-heads.net/avatar/{uuid}/40",
                                "role": "banned",
                                "ban_reason": reason,
                            }
            except Exception as e:
                logger.warning("Parse banned-players.json lỗi: %s", e)

        # 4. Kiểm tra ai đang Online thực tế qua API minecraft/players
        mc_data = await self._get(f"/api/client/servers/{server_id}/minecraft/players", ttl=ttl)
        if mc_data and isinstance(mc_data, dict):
            online_raw = mc_data.get("players", {})
            online_list = online_raw.get("list", []) if isinstance(online_raw, dict) else (online_raw if isinstance(online_raw, list) else [])
            for p in online_list:
                uuid = p.get("uuid") or p.get("id") or "" if isinstance(p, dict) else str(p)
                name = p.get("name") or p.get("username") or "" if isinstance(p, dict) else ""
                if uuid:
                    if uuid in players_map:
                        players_map[uuid]["status"] = "online"
                    else:
                        players_map[uuid] = {
                            "name": name or uuid[:8],
                            "uuid": uuid,
                            "status": "online",
                            "avatar": f"https://mc-heads.net/avatar/{uuid}/40",
                            "role": "player",
                        }

        # 4.1. Đối chiếu thời gian thực từ stream logs/latest.log (tránh độ trễ API panel)
        log_content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "logs/latest.log"}, ttl=2, is_json=False)
        if log_content:
            log_text = log_content.decode("utf-8", errors="ignore") if isinstance(log_content, bytes) else str(log_content)
            log_online_names: set[str] = set()
            for line in log_text.splitlines()[-250:]:
                m_j = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\s+joined the game", line)
                if not m_j:
                    m_j = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?player logged in as\s+([\w\.\_]+)\s+joined", line)
                if m_j:
                    log_online_names.add(m_j.group(2).lower())
                m_l = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\s+left the game", line)
                if not m_l:
                    m_l = re.search(r"\[(\d{2}:\d{2})\].*?:\s+.*?player logged in as\s+([\w\.\_]+)\s+disconnected", line)
                if not m_l and "lost connection" in line:
                    m_l = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\s+lost connection", line)
                if m_l:
                    log_online_names.discard(m_l.group(2).lower())

            for p in players_map.values():
                if p.get("name", "").lower() in log_online_names:
                    p["status"] = "online"

        # 5. Quét bổ sung các file player data (.dat) trong world/players/data hoặc world/playerdata
        for folder in ["world/players/data", "world/playerdata"]:
            file_data = await self._get(f"/api/client/servers/{server_id}/files/list", params={"directory": folder}, ttl=ttl)
            if file_data and isinstance(file_data, dict):
                entries = file_data.get("entries", {}).get("data", []) or file_data.get("data", [])
                for e in entries:
                    fname = e.get("name", "")
                    if fname.endswith(".dat") and not fname.endswith(".dat_old"):
                        uuid = fname[:-4]
                        if uuid and uuid not in players_map:
                            players_map[uuid] = {
                                "name": uuid[:8],
                                "uuid": uuid,
                                "status": "offline",
                                "avatar": f"https://mc-heads.net/avatar/{uuid}/40",
                                "role": "player",
                            }

        # Trả về danh sách được sắp xếp: Online trước -> Admin -> Banned -> Các người chơi khác
        role_prio = {"online": 0, "admin": 1, "banned": 2, "offline": 3}
        result = list(players_map.values())
        result.sort(key=lambda x: (
            0 if x.get("status") == "online" else (1 if x.get("role") == "admin" else (2 if x.get("status") == "banned" else 3)),
            x.get("name", "").lower()
        ))
        return result

    async def get_logs(self, server_id: str, limit: int = 100,
                        player_name: str | None = None, ttl: int = 60) -> list[dict[str, Any]]:
        """Lấy logs server (movement / fastbreak / chat ...). TTL 60s."""
        # 1. Thử đọc logs/latest.log từ panel file contents
        content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "logs/latest.log"}, ttl=ttl, is_json=False)
        if content and isinstance(content, (bytes, str)):
            text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
            lines = text.splitlines()
            parsed_logs: list[dict[str, Any]] = []
            for line in reversed(lines):
                if not line.strip():
                    continue
                if player_name and player_name.lower() not in line.lower():
                    continue
                category = "info"
                if "WARN" in line:
                    category = "warning"
                elif "ERROR" in line:
                    category = "error"
                elif any(k in line.lower() for k in ["moved too quickly", "moved wrongly", "flying", "speed", "fastbreak"]):
                    category = "movement"

                parsed_logs.append({
                    "raw": line,
                    "category": category,
                    "player": player_name or "server",
                })
                if len(parsed_logs) >= limit:
                    break
            return parsed_logs

        # 2. Fallback endpoint cũ
        params: dict[str, Any] = {"limit": limit}
        if player_name:
            params["player_name"] = player_name
        data = await self._get(f"/servers/{server_id}/logs", params=params, ttl=ttl)
        if data is None:
            return []
        return data if isinstance(data, list) else data.get("logs", [])

    async def get_player_stats(self, server_id: str, uuid: str,
                                ttl: int = 300) -> dict[str, Any]:
        """Stats đời chơi của player từ file json trong world/players/stats/{uuid}.json."""
        for path in [f"world/players/stats/{uuid}.json", f"world/stats/{uuid}.json"]:
            content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": path}, ttl=ttl, is_json=False)
            if content:
                try:
                    text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
                    parsed = json.loads(text)
                    stats_dict = parsed.get("stats", {})

                    flattened: dict[str, Any] = {}

                    # 1. Custom stats (play_time, distance, kills, deaths)
                    custom = stats_dict.get("minecraft:custom", {})
                    # Play time: ticks (20 ticks = 1 sec, 1200 ticks = 1 min, 72000 ticks = 1 hr)
                    play_ticks = (custom.get("minecraft:play_time") or
                                  custom.get("minecraft:total_world_time") or
                                  custom.get("minecraft:play_one_minute") or 0)
                    play_minutes = round(play_ticks / 1200.0, 2)
                    play_hours = round(play_ticks / 72000.0, 2)

                    flattened["play_time_ticks"] = play_ticks
                    flattened["play_time_minutes"] = play_minutes
                    flattened["play_time_hours"] = play_hours
                    flattened["play_hours"] = play_hours

                    # Distance traveled: sum of all centimeters (*_one_cm)
                    total_cm = sum(v for k, v in custom.items() if k.endswith("_one_cm") and isinstance(v, (int, float)))
                    distance_m = round(total_cm / 100.0, 2)
                    km_traveled = round(total_cm / 100000.0, 3)
                    flattened["distance_traveled"] = distance_m
                    flattened["km_traveled"] = km_traveled

                    # Combat stats
                    flattened["player_kills"] = custom.get("minecraft:player_kills", 0)
                    flattened["mob_kills"] = custom.get("minecraft:mob_kills", 0)
                    flattened["deaths"] = custom.get("minecraft:deaths", 0)

                    # 2. Mined stats (blocks broken & rare ores)
                    mined = stats_dict.get("minecraft:mined", {})
                    total_mined = sum(v for v in mined.values() if isinstance(v, (int, float)))
                    flattened["blocks_broken"] = total_mined

                    diamond = mined.get("minecraft:diamond_ore", 0) + mined.get("minecraft:deepslate_diamond_ore", 0)
                    emerald = mined.get("minecraft:emerald_ore", 0) + mined.get("minecraft:deepslate_emerald_ore", 0)
                    ancient_debris = mined.get("minecraft:ancient_debris", 0)
                    gold = (mined.get("minecraft:gold_ore", 0) + mined.get("minecraft:deepslate_gold_ore", 0) +
                            mined.get("minecraft:nether_gold_ore", 0))

                    flattened["diamond_ore_mined"] = diamond
                    flattened["emerald_ore_mined"] = emerald
                    flattened["ancient_debris_mined"] = ancient_debris
                    flattened["netherite_ore_mined"] = ancient_debris
                    flattened["gold_ore_mined"] = gold
                    flattened["rare_ores"] = diamond + emerald + ancient_debris

                    # Copy any other custom or mined stats
                    for category, values in stats_dict.items():
                        if isinstance(values, dict):
                            for k, v in values.items():
                                clean_k = k.replace("minecraft:", "")
                                if clean_k not in flattened:
                                    flattened[clean_k] = v

                    return flattened
                except Exception as e:
                    logger.warning("Parse stats %s lỗi: %s", path, e)

        # Fallback endpoint cũ
        data = await self._get(f"/servers/{server_id}/players/{uuid}/stats", ttl=ttl)
        return data or {}

    async def download_file(self, server_id: str, uuid: str,
                            filename: str = "playerdata.dat") -> bytes | None:
        """Tải buffer file .dat (GET) để parse NBT."""
        candidates = [
            f"world/players/data/{uuid}.dat",
            f"world/playerdata/{uuid}.dat",
            f"world/players/data/{filename}",
            f"world/playerdata/{filename}",
        ]
        for path in candidates:
            content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": path}, ttl=120, is_json=False)
            if content and isinstance(content, bytes) and len(content) > 0:
                return content

        return None

    async def get_player_data(self, server_id: str, uuid: str, ttl: int = 300) -> dict[str, Any]:
        """Danh sách file data (.dat) của player."""
        data = await self._get(f"/servers/{server_id}/players/{uuid}/data", ttl=ttl)
        return data or {}

    async def get_player_forensics(self, server_id: str, uuid: str, ttl: int = 120) -> dict[str, Any]:
        """Dữ liệu forensics tổng hợp."""
        data = await self._get(f"/servers/{server_id}/players/{uuid}/forensics", ttl=ttl)
        return data or {}

    async def get_server_overview(self, server_id: str, ttl: int = 30) -> dict[str, Any]:
        """Lấy toàn bộ thông tin chi tiết của server để quản lý tổng thể."""
        overview: dict[str, Any] = {
            "server_id": server_id,
            "name": "Minecraft Server",
            "uuid": server_id,
            "address": "localhost:25565",
            "limits": {"cpu": 100, "memory": 2048, "disk": 10240},
            "resources": {
                "state": "running",
                "memory_mb": 0,
                "memory_limit_mb": 0,
                "disk_mb": 0,
                "cpu_percent": 0,
                "network_rx_mb": 0,
                "network_tx_mb": 0,
            },
            "properties": {
                "motd": "A Minecraft Server",
                "max_players": "20",
                "gamemode": "survival",
                "difficulty": "normal",
                "pvp": "true",
                "view_distance": "10",
                "online_mode": "false",
                "version": "Paper 26.2 (Java 25)",
            },
            "ops": [],
            "banned_players": [],
            "total_registered_players": 0,
        }

        # 1. Thông tin server & allocation
        detail_data = await self._get(f"/api/client/servers/{server_id}", ttl=ttl)
        if detail_data and isinstance(detail_data, dict):
            s = detail_data.get("server", detail_data.get("attributes", detail_data))
            alloc = s.get("allocation", {})
            overview["name"] = s.get("name", "Minecraft Server")
            overview["uuid"] = s.get("uuid", server_id)
            host = alloc.get("ip_alias") or alloc.get("ip") or "matrix.pikamc.vn"
            port = alloc.get("port", 25565)
            overview["address"] = f"{host}:{port}"
            overview["limits"] = s.get("limits", overview["limits"])

        # 2. Tài nguyên thời gian thực (CPU, RAM, Ổ cứng, Mạng)
        res_data = await self._get(f"/api/client/servers/{server_id}/resources", ttl=10)
        if res_data and isinstance(res_data, dict):
            res = res_data.get("resources", res_data.get("attributes", res_data))
            mem_bytes = res.get("memory_bytes", 0)
            mem_limit = res.get("memory_limit_bytes", 0) or (overview["limits"].get("memory", 10240) * 1024 * 1024)
            disk_bytes = res.get("disk_bytes", 0)
            net = res.get("network", {})
            overview["resources"] = {
                "state": res.get("state", "running"),
                "memory_mb": round(mem_bytes / (1024 * 1024), 1),
                "memory_limit_mb": round(mem_limit / (1024 * 1024), 1),
                "disk_mb": round(disk_bytes / (1024 * 1024), 1),
                "cpu_percent": round(res.get("cpu_absolute", 0), 1),
                "network_rx_mb": round(net.get("rx_bytes", 0) / (1024 * 1024), 2),
                "network_tx_mb": round(net.get("tx_bytes", 0) / (1024 * 1024), 2),
            }

        # 3. server.properties
        prop_content = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "server.properties"}, ttl=ttl, is_json=False)
        if prop_content:
            try:
                text = prop_content.decode("utf-8", errors="ignore") if isinstance(prop_content, bytes) else prop_content
                props = {}
                for line in text.splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
                overview["properties"].update({
                    "motd": props.get("motd", "A Minecraft Server"),
                    "max_players": props.get("max-players", "20"),
                    "gamemode": props.get("gamemode", "survival"),
                    "difficulty": props.get("difficulty", "normal"),
                    "pvp": props.get("pvp", "true"),
                    "view_distance": props.get("view-distance", "10"),
                    "online_mode": props.get("online-mode", "false"),
                })
            except Exception as e:
                logger.warning("Parse server.properties lỗi: %s", e)

        # 4. Ops & Banned
        ops_c = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "ops.json"}, ttl=ttl, is_json=False)
        if ops_c:
            try:
                t = ops_c.decode("utf-8", errors="ignore") if isinstance(ops_c, bytes) else ops_c
                overview["ops"] = json.loads(t)
            except Exception:
                overview["ops"] = []

        bans_c = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "banned-players.json"}, ttl=ttl, is_json=False)
        if bans_c:
            try:
                t = bans_c.decode("utf-8", errors="ignore") if isinstance(bans_c, bytes) else bans_c
                overview["banned_players"] = json.loads(t)
            except Exception:
                overview["banned_players"] = []

        # 5. usercache count
        uc_c = await self._get(f"/api/client/servers/{server_id}/files/contents", params={"file": "usercache.json"}, ttl=ttl, is_json=False)
        if uc_c:
            try:
                t = uc_c.decode("utf-8", errors="ignore") if isinstance(uc_c, bytes) else uc_c
                overview["total_registered_players"] = len(json.loads(t))
            except Exception:
                overview["total_registered_players"] = 0

        return overview

    async def get_live_activity(self, server_id: str, limit: int = 30) -> dict[str, Any]:
        """Phân tích real-time các sự kiện người chơi vào/ra server từ logs/latest.log."""
        log_content = await self._get(
            f"/api/client/servers/{server_id}/files/contents",
            params={"file": "logs/latest.log"},
            ttl=2,
            is_json=False
        )
        events: list[dict[str, Any]] = []
        if log_content:
            text = log_content.decode("utf-8", errors="ignore") if isinstance(log_content, bytes) else str(log_content)
            for line in text.splitlines():
                # Join: hỗ trợ các tiền tố clan, rank như 'NTP .NoxrynX joined the game' hoặc Floodgate
                m_join = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\s+joined the game", line)
                if not m_join:
                    m_join = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?player logged in as\s+([\w\.\_]+)\s+joined", line)
                if m_join:
                    events.append({
                        "type": "join",
                        "time": m_join.group(1),
                        "player": m_join.group(2),
                        "message": f"Người chơi {m_join.group(2)} vừa tham gia server"
                    })
                    continue

                # Leave: hỗ trợ các tiền tố clan, rank hoặc disconnect
                m_leave = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\s+left the game", line)
                if not m_leave:
                    m_leave = re.search(r"\[(\d{2}:\d{2})\].*?:\s+.*?player logged in as\s+([\w\.\_]+)\s+disconnected", line)
                if not m_leave and "lost connection" in line:
                    m_leave = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\s+lost connection", line)
                if m_leave:
                    events.append({
                        "type": "leave",
                        "time": m_leave.group(1),
                        "player": m_leave.group(2),
                        "message": f"Người chơi {m_leave.group(2)} đã rời server"
                    })
                    continue

                m_login = re.search(r"\[(\d{2}:\d{2}:\d{2})\].*?:\s+.*?([\w\.\_]+)\[/([0-9\.]+):.*?logged in", line)
                if m_login:
                    events.append({
                        "type": "login",
                        "time": m_login.group(1),
                        "player": m_login.group(2),
                        "ip": m_login.group(3),
                        "message": f"Kết nối: {m_login.group(2)} (IP: {m_login.group(3)})"
                    })
                    continue

        events.reverse()  # Mới nhất lên đầu
        return {
            "server_id": server_id,
            "events_count": len(events),
            "events": events[:limit],
            "latest_join": next((e for e in events if e["type"] in ("join", "login")), None),
        }

    async def get_world_overview(self, server_id: str, ttl: int = 15) -> dict[str, Any]:
        """Tổng hợp thông tin chi tiết về Thế Giới Minecraft (world data) thời gian thực."""
        overview: dict[str, Any] = {
            "server_id": server_id,
            "level_name": "world",
            "time_ticks": 0,
            "world_age_days": 0.0,
            "world_age_hours": 0.0,
            "time_of_day_phase": "Ban ngày",
            "game_type": "Sinh tồn (Survival)",
            "difficulty": "Bình thường (Normal)",
            "version": "Paper 26.2",
            "player_data_files": 0,
            "player_stats_files": 0,
            "advancement_files": 0,
        }

        # 1. Đọc world/level.dat
        content = await self._get(
            f"/api/client/servers/{server_id}/files/contents",
            params={"file": "world/level.dat"},
            ttl=ttl,
            is_json=False
        )
        if content and isinstance(content, bytes):
            try:
                root = parse_dat_buffer(content)
                if root:
                    data = _to_plain(root.get("Data", {}))
                    time_ticks = int(data.get("Time") or 0)
                    day_time = int(data.get("DayTime") or time_ticks)
                    ver = data.get("Version", {})
                    v_raw = ver.get("Name", "Paper 26.2") if isinstance(ver, dict) else ver
                    v_name = str(v_raw)

                    # Phân loại thời gian trong ngày
                    tod_ticks = day_time % 24000
                    if tod_ticks < 12000:
                        tod_phase = f"☀️ Ban ngày ({tod_ticks // 1000}h Minecraft)"
                    elif tod_ticks < 13800:
                        tod_phase = "🌅 Hoàng hôn"
                    elif tod_ticks < 22200:
                        tod_phase = f"🌙 Ban đêm ({tod_ticks // 1000}h Minecraft)"
                    else:
                        tod_phase = "🌄 Bình minh"

                    gt_map = {0: "Sinh tồn (Survival)", 1: "Sáng tạo (Creative)", 2: "Phiêu lưu (Adventure)", 3: "Khán giả (Spectator)"}
                    game_type = gt_map.get(int(data.get("GameType") or 0), "Sinh tồn (Survival)")

                    diff_map = {0: "Hòa bình (Peaceful)", 1: "Dễ (Easy)", 2: "Bình thường (Normal)", 3: "Khó (Hard)"}
                    diff = diff_map.get(int(data.get("Difficulty") or 2), "Bình thường (Normal)")

                    overview.update({
                        "level_name": str(data.get("LevelName") or "world"),
                        "time_ticks": time_ticks,
                        "world_age_days": round(time_ticks / 24000.0, 1),
                        "world_age_hours": round(time_ticks / 72000.0, 1),
                        "time_of_day_phase": tod_phase,
                        "game_type": game_type,
                        "difficulty": diff,
                        "version": v_name,
                    })
            except Exception as e:
                logger.warning("Parse world/level.dat lỗi: %s", e)

        # 2. Đếm số file trong world/players
        for folder, key in [
            ("world/players/data", "player_data_files"),
            ("world/players/stats", "player_stats_files"),
            ("world/players/advancements", "advancement_files")
        ]:
            f_data = await self._get(f"/api/client/servers/{server_id}/files/list", params={"directory": folder}, ttl=ttl)
            if f_data and isinstance(f_data, dict):
                entries = f_data.get("entries", {}).get("data", []) or f_data.get("data", [])
                overview[key] = len(entries)

        return overview

    async def get_log_files(self, server_id: str, ttl: int = 30) -> list[dict[str, Any]]:
        """Lấy danh sách toàn bộ các file log trên server (latest.log và các file lưu trữ .log.gz)."""
        data = await self._get(f"/api/client/servers/{server_id}/files/list", params={"directory": "logs"}, ttl=ttl)
        if not data or not isinstance(data, dict):
            return [{"name": "latest.log", "size_kb": 0, "is_current": True, "date": "Đang chạy (Real-time)"}]

        entries = data.get("entries", {}).get("data", []) or data.get("data", [])
        log_files: list[dict[str, Any]] = []

        has_latest = False
        for e in entries:
            name = e.get("name", "")
            if name == "latest.log":
                has_latest = True
                size = e.get("size", 0)
                log_files.append({
                    "name": "latest.log",
                    "size_kb": round(size / 1024.0, 1),
                    "is_current": True,
                    "date": "Đang chạy (Real-time)",
                })
            elif name.endswith(".log.gz") or name.endswith(".log"):
                size = e.get("size", 0)
                m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
                date_str = m.group(1) if m else "Lưu trữ"
                log_files.append({
                    "name": name,
                    "size_kb": round(size / 1024.0, 1),
                    "is_current": False,
                    "date": date_str,
                })

        if not has_latest:
            log_files.insert(0, {
                "name": "latest.log",
                "size_kb": 0,
                "is_current": True,
                "date": "Đang chạy (Real-time)",
            })

        current = [f for f in log_files if f["is_current"]]
        archives = sorted([f for f in log_files if not f["is_current"]], key=lambda x: x["name"], reverse=True)
        return current + archives

    async def read_log_file(self, server_id: str, filename: str = "latest.log",
                             limit: int = 300, category: str | None = None,
                             search: str | None = None) -> dict[str, Any]:
        """Đọc và giải mã bất kỳ file log nào (latest.log hoặc file nén .log.gz toàn thời gian)."""
        file_path = filename if filename.startswith("logs/") else f"logs/{filename}"
        ttl = 2 if "latest.log" in filename else 600

        content = await self._get(
            f"/api/client/servers/{server_id}/files/contents",
            params={"file": file_path},
            ttl=ttl,
            is_json=False
        )
        if not content:
            return {"filename": filename, "total_lines": 0, "matched_lines": 0, "violations_count": 0, "lines": []}

        text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)
        all_lines = text.splitlines()

        parsed_lines: list[dict[str, Any]] = []
        violations_count = 0

        for idx, line in enumerate(reversed(all_lines)):
            line_str = line.strip()
            if not line_str:
                continue

            lvl = "INFO"
            cat = "info"
            if "WARN" in line_str:
                lvl = "WARN"
                cat = "warning"
            elif "ERROR" in line_str or "Exception" in line_str:
                lvl = "ERROR"
                cat = "error"

            lower_line = line_str.lower()
            if any(k in lower_line for k in ["moved too quickly", "moved wrongly", "flying", "fastbreak", "speed", "cheat"]):
                cat = "movement"
                violations_count += 1
            elif "joined the game" in lower_line or "logged in" in lower_line:
                cat = "join"
            elif "left the game" in lower_line or "lost connection" in lower_line:
                cat = "leave"
            elif "<" in line_str and ">" in line_str:
                cat = "chat"

            if category and category.lower() != "all" and cat != category.lower():
                continue
            if search and search.lower() not in lower_line:
                continue

            m_time = re.search(r"\[(\d{2}:\d{2}:\d{2})\]", line_str)
            t_str = m_time.group(1) if m_time else ""

            parsed_lines.append({
                "line_no": len(all_lines) - idx,
                "time": t_str,
                "level": lvl,
                "category": cat,
                "text": line_str,
            })

            if len(parsed_lines) >= limit:
                break

        return {
            "filename": filename,
            "total_lines": len(all_lines),
            "matched_lines": len(parsed_lines),
            "violations_count": violations_count,
            "lines": parsed_lines,
        }

    async def get_realtime_pulse(self, server_id: str) -> dict[str, Any]:
        """Tổng hợp toàn bộ nhịp đập Real-time (Tài nguyên, Người chơi Online, Sự kiện mới, World stats)."""
        t_res, t_mc, t_act, t_world = await asyncio.gather(
            self._get(f"/api/client/servers/{server_id}/resources", ttl=2),
            self._get(f"/api/client/servers/{server_id}/minecraft/players", ttl=3),
            self.get_live_activity(server_id, limit=10),
            self.get_world_overview(server_id, ttl=15),
            return_exceptions=True
        )

        resources = {
            "state": "running",
            "memory_mb": 0,
            "memory_limit_mb": 10240,
            "cpu_percent": 0.0,
            "disk_mb": 0,
            "network_rx_mb": 0.0,
            "network_tx_mb": 0.0,
        }
        if t_res and isinstance(t_res, dict) and not isinstance(t_res, Exception):
            res = t_res.get("resources", t_res.get("attributes", t_res))
            net = res.get("network", {})
            resources = {
                "state": res.get("state", "running"),
                "memory_mb": round(res.get("memory_bytes", 0) / (1024 * 1024), 1),
                "memory_limit_mb": round(res.get("memory_limit_bytes", 0) / (1024 * 1024), 1) or 10240,
                "cpu_percent": round(res.get("cpu_absolute", 0.0), 1),
                "disk_mb": round(res.get("disk_bytes", 0) / (1024 * 1024), 1),
                "network_rx_mb": round(net.get("rx_bytes", 0) / (1024 * 1024), 2),
                "network_tx_mb": round(net.get("tx_bytes", 0) / (1024 * 1024), 2),
            }

        online_players: list[dict[str, Any]] = []
        server_reported = 0
        if t_mc and isinstance(t_mc, dict) and not isinstance(t_mc, Exception):
            online_raw = t_mc.get("players", {})
            server_reported = int(online_raw.get("online") or 0)
            p_list = online_raw.get("list", []) if isinstance(online_raw, dict) else (online_raw if isinstance(online_raw, list) else [])
            for p in p_list:
                uuid = p.get("uuid") or p.get("id") or "" if isinstance(p, dict) else str(p)
                name = p.get("name") or p.get("username") or "" if isinstance(p, dict) else ""
                online_players.append({
                    "name": name or (uuid[:8] if uuid else "Steve"),
                    "uuid": uuid,
                    "avatar": f"https://mc-heads.net/avatar/{uuid}/32" if uuid else "https://mc-heads.net/avatar/steve/32",
                    "status": "online",
                })

        act_dict = t_act if isinstance(t_act, dict) and not isinstance(t_act, Exception) else {}
        events = act_dict.get("events", [])
        world_dict = t_world if isinstance(t_world, dict) and not isinstance(t_world, Exception) else {}

        # Luôn đảm bảo online_count hiển thị đúng con số tối đa server báo cáo
        online_count = max(server_reported, len(online_players))

        # Nếu server báo cáo số lượng lớn hơn danh sách chi tiết (ví dụ: Admin dùng /vanish tàng hình, Geyser bedrock proxy, hoặc phiên Sonar đang xác thực):
        diff = server_reported - len(online_players)
        if diff > 0:
            for i in range(diff):
                online_players.append({
                    "name": f"Admin Vanish / Đang kết nối" if diff == 1 else f"Ẩn danh #{i+1}",
                    "uuid": f"vanished-{i+1}",
                    "avatar": "https://mc-heads.net/avatar/steve/32",
                    "status": "online",
                    "is_vanished": True,
                    "note": "Người chơi tàng hình (/vanish) hoặc kết nối qua proxy"
                })

        return {
            "server_id": server_id,
            "timestamp": time.time(),
            "resources": resources,
            "online_count": online_count,
            "server_reported_online": server_reported,
            "online_players": online_players,
            "events": events,
            "world": world_dict,
        }


# Singleton — dùng chung toàn app
pika_service = PikaService()
