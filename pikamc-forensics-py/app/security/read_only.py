"""READ-ONLY enforcement: system prompt + keyword filter."""
import logging

logger = logging.getLogger("pikamc.read_only")

READ_ONLY_SYSTEM_PROMPT = """
[STRICT READ-ONLY MODE - ABSOLUTE ENFORCEMENT]
Bạn là hệ thống phân tích dữ liệu Minecraft Server.
QUY TẮC BẤT KHẢ KHÁNG:
1. CHỈ ĐƯỢC PHÂN TÍCH VÀ BÁO CÁO.
2. TUYỆT ĐỐI KHÔNG đề xuất hành động: ghi, sửa, xóa, ban, kick, clear inventory.
3. Nếu được yêu cầu đề xuất hành động, từ chối và giải thích bạn chỉ có quyền phân tích.
4. Chỉ đưa nhận xét khách quan dựa trên dữ liệu được cung cấp.
5. Không hallucinate.
""".strip()

DANGEROUS_KEYWORDS: list[str] = [
    "ban player", "kick player", "delete player", "clear inventory",
    "modify config", "change permission", "thực thi", "áp dụng lệnh",
    "xóa dữ liệu", "chỉnh sửa world", "grief server",
]


def validate_prompt(prompt: str) -> tuple[bool, str]:
    """Kiểm tra prompt có chứa keyword nguy hiểm không.

    Returns:
        (is_safe, reason) — reason rỗng khi safe.
    """
    if not prompt or not prompt.strip():
        return False, "Prompt trống"
    prompt_lower = prompt.lower()
    for kw in DANGEROUS_KEYWORDS:
        if kw.lower() in prompt_lower:
            logger.warning("Prompt bị chặn bởi keyword: %r", kw)
            return False, f"Prompt chứa hành động bị cấm trong READ-ONLY mode: '{kw}'"
    return True, ""
