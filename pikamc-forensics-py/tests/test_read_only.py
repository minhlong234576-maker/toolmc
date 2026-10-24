"""Test READ-ONLY: keyword filter + system prompt."""
from app.security.read_only import DANGEROUS_KEYWORDS, READ_ONLY_SYSTEM_PROMPT, validate_prompt


class TestValidatePrompt:
    def test_safe_prompt(self):
        ok, reason = validate_prompt("Phân tích hành vi mining của player X")
        assert ok is True
        assert reason == ""

    def test_empty_prompt(self):
        ok, _ = validate_prompt("   ")
        assert ok is False

    def test_dangerous_english(self):
        for kw in ["ban player", "kick player", "delete player", "clear inventory",
                   "modify config", "change permission"]:
            ok, reason = validate_prompt(f"Please {kw.upper()} NOW")
            assert ok is False, kw
            assert "cấm" in reason or kw in reason

    def test_dangerous_vietnamese(self):
        ok, _ = validate_prompt("Hãy thực thi lệnh này")
        assert ok is False

    def test_all_keywords_present(self):
        assert len(DANGEROUS_KEYWORDS) >= 8


class TestSystemPrompt:
    def test_contains_rules(self):
        assert "READ-ONLY" in READ_ONLY_SYSTEM_PROMPT
        assert "CHỈ ĐƯỢC PHÂN TÍCH" in READ_ONLY_SYSTEM_PROMPT
        assert "hallucinate" in READ_ONLY_SYSTEM_PROMPT
