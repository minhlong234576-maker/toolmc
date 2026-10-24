"""Parse file NBT .dat của Minecraft bằng nbtlib."""
import gzip
import io
import logging
import zlib
from typing import Any

from nbtlib import File
from nbtlib.tag import Compound, List

logger = logging.getLogger("pikamc.nbt_service")

# Định nghĩa vật phẩm hiếm/quý (ores) để đếm trong inventory
RARE_ORE_IDS = {"minecraft:diamond", "minecraft:emerald", "minecraft:netherite_ingot",
                "minecraft:ancient_debris", "minecraft:diamond_ore", "minecraft:emerald_ore"}

# Vật phẩm không được phép tồn tại trong inventory người chơi hợp lệ
ILLEGAL_ITEM_IDS = {"minecraft:command_block", "minecraft:chain_command_block",
                    "minecraft:repeating_command_block", "minecraft:barrier",
                    "minecraft:bedrock", "minecraft:spawner"}

MAX_ENCHANT_LEVEL = 5  # enchant > safe mặc định


def parse_dat_buffer(raw: bytes) -> Compound | None:
    """Parse buffer .dat (NBT). Xử lý gzip/zlib/raw. Trả None khi lỗi."""
    data: bytes = raw
    # Thử auto-decompress
    if raw[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(raw)
        except Exception as e:  # noqa: BLE001
            logger.error("gzip decompress lỗi: %s", e)
            return None
    elif raw[:1] not in (b"\x0a",):
        try:
            data = zlib.decompress(raw)
        except Exception:  # noqa: BLE001
            data = raw  # sẵn raw NBT
    try:
        root = File.parse(io.BytesIO(data))
        return root
    except Exception as e:  # noqa: BLE001
        logger.error("NBT parse lỗi: %s", e)
        return None


def _to_plain(tag: Any) -> Any:
    """Chuyển NBT tag thành kiểu Python thuần (dict/list/str/int...)."""
    if isinstance(tag, Compound):
        return {k: _to_plain(v) for k, v in tag.items()}
    if isinstance(tag, List):
        return [_to_plain(v) for v in tag]
    # Casing compound + any scalar int/float/string
    if hasattr(tag, "as_int"):
        return int(tag)
    try:
        val = tag.real if hasattr(tag, "real") else tag
        if isinstance(val, (int, float, str, bool)):
            return val
    except Exception:
        return str(tag)
    return str(tag) if not isinstance(tag, (int, float, str, bool)) else tag


def extract_inventory(root: Compound) -> list[dict[str, Any]]:
    """Extract Inventory: id, count, enchantments."""
    items: list[dict[str, Any]] = []
    plain = _to_plain(root)
    inv = (plain.get("Inventory") or []) if isinstance(plain, dict) else []
    for entry in inv:
        if not isinstance(entry, dict):
            continue
        item: dict[str, Any] = {
            "id": entry.get("id", ""),
            "count": int(entry.get("Count", 1)),
            "slot": entry.get("Slot", -1),
            "ench": [],
        }
        # NBT tag -> enchantments
        tag = entry.get("tag") or {}
        comps = tag.get("Enchantments") or tag.get("ench") or []
        for e in comps if isinstance(comps, list) else []:
            if isinstance(e, dict):
                # Minecraft cũ dùng "lvl", 1.20.5+ dùng "levels"
                lvl = e.get("lvl", e.get("levels", 0))
                item["ench"].append({
                    "id": e.get("id", ""),
                    "lvl": int(lvl or 0),
                })

        # Hỗ trợ Minecraft 1.20.5+ / 1.21+ components format
        components = entry.get("components") or {}
        if isinstance(components, dict):
            ench_dict = components.get("minecraft:enchantments") or {}
            levels = ench_dict.get("levels", {}) if isinstance(ench_dict, dict) else ench_dict
            if isinstance(levels, dict):
                for ench_id, ench_lvl in levels.items():
                    item["ench"].append({
                        "id": str(ench_id),
                        "lvl": int(ench_lvl or 0),
                    })
        items.append(item)
    return items


def extract_player_flags(root: Compound) -> dict[str, Any]:
    """Extract abilities (mayfly/invulnerable), Motion, Pos."""
    plain = _to_plain(root)
    out: dict[str, Any] = {}
    if not isinstance(plain, dict):
        return out

    abilities = plain.get("abilities") or {}
    out["mayfly"] = bool(abilities.get("mayfly", 0))
    out["invulnerable"] = bool(plain.get("Invulnerable", 0))

    motion = plain.get("Motion")
    if isinstance(motion, list) and len(motion) == 3:
        out["motion_x"] = float(motion[0]); out["motion_y"] = float(motion[1]); out["motion_z"] = float(motion[2])

    pos = plain.get("Pos")
    if isinstance(pos, list) and len(pos) == 3:
        out["pos_x"] = float(pos[0]); out["pos_y"] = float(pos[1]); out["pos_z"] = float(pos[2])

    return out


def parse_player_dat(raw: bytes) -> dict[str, Any]:
    """Hàm chính: parse buffer .dat -> { inventory, flags, raw_entities }."""
    root = parse_dat_buffer(raw)
    if root is None:
        return {"ok": False, "inventory": [], "flags": {}}
    return {
        "ok": True,
        "inventory": extract_inventory(root),
        "flags": extract_player_flags(root),
    }


def is_suspicious_item(item: dict[str, Any]) -> tuple[bool, list[str]]:
    """Phát hiện: command_block, barrier, stack > 64, ench > MAX_ENCHANT_LEVEL.

    Returns:
        (is_suspicious, reasons)
    """
    reasons: list[str] = []
    item_id = str(item.get("id", ""))
    count = int(item.get("count", 1))

    if item_id in ILLEGAL_ITEM_IDS:
        reasons.append(f"Illegal item: {item_id}")

    if count > 64:
        reasons.append(f"Stack vượt giới hạn: {count} x {item_id}")
    elif count > 1 and any(unstack in item_id for unstack in ("sword", "helmet", "chestplate", "leggings", "boots", "totem_of_undying", "elytra", "shulker_box")):
        reasons.append(f"Vật phẩm không thể cộng dồn bị xếp chồng: {count} x {item_id}")
    elif count > 16 and "ender_pearl" in item_id:
        reasons.append(f"Ender pearl vượt stack tối đa 16: {count}")

    for e in item.get("ench", []):
        lvl = int(e.get("lvl", 0))
        if lvl > MAX_ENCHANT_LEVEL:
            reasons.append(f"Enchant bất thường: {e.get('id')} lvl={lvl}")
    return (len(reasons) > 0, reasons)


def count_rare_ores_inventory(inventory: list[dict[str, Any]]) -> int:
    """Đếm tổng số rare ores trong inventory."""
    total = 0
    for item in inventory:
        if str(item.get("id", "")) in RARE_ORE_IDS:
            total += int(item.get("count", 1))
    return total
