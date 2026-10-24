"""AI providers: Gemini / OpenAI / Ollama + READ-ONLY system prompt."""
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings

logger = logging.getLogger("pikamc.ai_service")

settings = get_settings()


@dataclass
class AIOptions:
    """Tùy chọn gọi AI."""
    model: str | None = None
    temperature: float = 0.3
    max_tokens: int = 2048
    api_key: str | None = None
    api_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AIProvider(ABC):
    """Abstract base class cho mọi AI provider."""

    name: str = "abstract"
    default_model: str = ""

    @abstractmethod
    def analyze(self, prompt: str, system_prompt: str, options: AIOptions) -> str:
        """Gọi model và trả về chuỗi response (text)."""

    def is_available(self) -> bool:
        """Provider có api key/host cấu hình không."""
        return True


class GeminiProvider(AIProvider):
    """Google Gemini qua google-generativeai SDK."""

    name = "gemini"
    default_model = "gemini-1.5-flash"

    def __init__(self) -> None:
        self.available = bool(settings.GEMINI_API_KEY)

    def is_available(self) -> bool:
        return bool(get_settings().GEMINI_API_KEY)

    def analyze(self, prompt: str, system_prompt: str, options: AIOptions) -> str:
        import google.generativeai as genai
        genai.configure(api_key=get_settings().GEMINI_API_KEY)
        model_id = options.model or self.default_model
        model = genai.GenerativeModel(
            model_name=model_id,
            system_instruction=system_prompt,
        )
        result = model.generate_content(
            prompt,
            generation_config={
                "temperature": options.temperature,
                "max_output_tokens": options.max_tokens,
            },
        )
        return result.text or ""


class OpenAIProvider(AIProvider):
    """OpenAI ChatCompletions với system prompt READ-ONLY."""

    name = "openai"
    default_model = "gpt-4o-mini"

    def __init__(self) -> None:
        self.available = bool(settings.OPENAI_API_KEY)

    def is_available(self) -> bool:
        return bool(get_settings().OPENAI_API_KEY)

    def analyze(self, prompt: str, system_prompt: str, options: AIOptions) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=get_settings().OPENAI_API_KEY)
        model_id = options.model or self.default_model
        resp = client.chat.completions.create(
            model=model_id,
            temperature=options.temperature,
            max_tokens=options.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""


class OllamaProvider(AIProvider):
    """Ollama local qua ollama SDK."""

    name = "ollama"
    default_model = "qwen2.5:0.5b"

    def analyze(self, prompt: str, system_prompt: str, options: AIOptions) -> str:
        import ollama
        client = ollama.Client(host=settings.OLLAMA_HOST)
        model_id = options.model or self.default_model

        # Tự động phát hiện model có sẵn trong local Ollama
        try:
            available_list = client.list().get("models", [])
            model_names = [m.get("name", m.get("model", "")) for m in available_list]
            if model_names:
                # Nếu model yêu cầu chưa có, chọn model đầu tiên khả dụng
                if not any(model_id in name for name in model_names):
                    model_id = model_names[0]
            else:
                raise RuntimeError(
                    f"Ollama đang chạy tại {settings.OLLAMA_HOST} nhưng chưa có model nào! "
                    f"Vui lòng chạy: ollama pull qwen2.5:0.5b hoặc ollama pull llama3.2 trong terminal."
                )
        except Exception as e:
            if "chưa có model" in str(e):
                raise
            logger.warning("Không kiểm tra được model Ollama: %s", e)

        try:
            resp = client.chat(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": options.temperature},
            )
            return resp["message"]["content"]
        except Exception as e:
            logger.exception("Lỗi khi gọi Ollama chat:")
            raise RuntimeError(f"Ollama chat lỗi (model: {model_id}): {e}") from e


class MetaAIProvider(AIProvider):
    """Meta AI / Llama Developer API (dev.meta.ai / api.meta.ai)."""

    name = "meta"
    default_model = "llama-3.3-70b-instruct"

    def __init__(self) -> None:
        self.available = bool(settings.META_API_KEY)

    def is_available(self) -> bool:
        return bool(get_settings().META_API_KEY)

    def analyze(self, prompt: str, system_prompt: str, options: AIOptions) -> str:
        import httpx

        curr_settings = get_settings()
        api_key = curr_settings.META_API_KEY
        if not api_key:
            raise RuntimeError(
                "Chưa cấu hình META_API_KEY trong .env! "
                "Lấy API key tại: https://dev.meta.ai/api-keys?team_id=1163733282980704&project_id=2093527077961508"
            )

        base_url = curr_settings.META_API_URL.strip().rstrip("/")
        endpoint = f"{base_url}/chat/completions"
        model_id = options.model or curr_settings.META_MODEL or self.default_model

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                if resp.status_code != 200:
                    err_msg = resp.text
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message") or err_json.get("message") or err_msg
                    except Exception:
                        pass
                    raise RuntimeError(f"Meta AI HTTP {resp.status_code}: {err_msg}")

                data = resp.json()
                choices = data.get("choices", [])
                if choices and isinstance(choices, list):
                    first_choice = choices[0]
                    msg = first_choice.get("message", {})
                    return msg.get("content") or ""
                return ""
        except Exception as e:
            logger.exception("Lỗi khi gọi Meta AI chat:")
            raise RuntimeError(f"Meta AI chat lỗi (model: {model_id}): {e}") from e


XAI247_VALID_MODELS = {
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5.5",
    "claude-fable-5",
    "claude-fable-5.1",
    "gpt-6-luna",
    "qwen3.8-flash",
    "qwen3.8-max",
    "glm-5.2",
    "glm-5.3",
    "glm-5.3-flash",
    "kimi-k3",
}

XAI247_MODEL_ALIASES = {
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-3.5-sonnet": "claude-sonnet-4-6",
    "claude-3-sonnet": "claude-sonnet-4-6",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-3-opus": "claude-opus-5",
    "claude-opus": "claude-opus-5",
    "claude-fable": "claude-fable-5",
    "deepseek-4.1-flash": "deepseek-v4.1-flash",
    "deepseek-flash": "deepseek-v4.1-flash",
    "deepseek-v4-flash": "deepseek-v4.1-flash",
    "deepseek-v4": "deepseek-v4-pro",
    "deepseek-r1": "deepseek-v4-pro",
    "deepseek-v3": "deepseek-v4-pro",
    "deepseek-chat": "deepseek-v4.1-flash",
    "gpt-4o": "gpt-6-luna",
    "gpt-4o-mini": "gpt-6-luna",
    "gpt-4": "gpt-6-luna",
    "qwen": "qwen3.8-flash",
    "glm": "glm-5.3-flash",
}


def resolve_xai247_model(raw_name: str | None) -> str:
    """Chuẩn hóa model ID theo danh sách thực tế của xai247.xyz, tránh lỗi Model not found."""
    if not raw_name or not raw_name.strip():
        return "deepseek-v4.1-flash"
    clean = raw_name.strip().lower()
    if clean in XAI247_MODEL_ALIASES:
        return XAI247_MODEL_ALIASES[clean]
    if clean in XAI247_VALID_MODELS:
        return clean
    logger.warning("Model '%s' không có trong danh mục xai247.xyz, tự động dùng 'deepseek-v4.1-flash'", raw_name)
    return "deepseek-v4.1-flash"


class XAI247Provider(AIProvider):
    """XAI247 AI Gateway (xai247.xyz - hỗ trợ Claude, GPT-4o, DeepSeek, Llama qua 1 API key duy nhất)."""

    name = "xai247"
    default_model = "deepseek-v4.1-flash"

    def __init__(self) -> None:
        self.available = bool(settings.XAI247_API_KEY)

    def is_available(self) -> bool:
        return bool(get_settings().XAI247_API_KEY)

    def analyze(self, prompt: str, system_prompt: str, options: AIOptions) -> str:
        import httpx

        curr_settings = get_settings()
        api_key = (options.api_key or curr_settings.XAI247_API_KEY or "").strip()
        if not api_key:
            raise RuntimeError(
                "Chưa có XAI247 API Key! "
                "Vui lòng nhập API key vào Cài Đặt hoặc lấy key tại: https://xai247.xyz"
            )

        raw_url = (options.api_url or curr_settings.XAI247_API_URL or "https://xai247.xyz/v1").strip().rstrip("/")
        if "/dashboard" in raw_url:
            base_url = "https://xai247.xyz/v1"
        elif not raw_url.endswith("/v1"):
            base_url = f"{raw_url}/v1"
        else:
            base_url = raw_url

        endpoint = f"{base_url}/chat/completions"
        model_id = resolve_xai247_model(options.model or curr_settings.XAI247_MODEL or self.default_model)

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": options.temperature,
            "max_tokens": options.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            with httpx.Client(timeout=45.0) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                if resp.status_code != 200:
                    err_msg = resp.text
                    try:
                        err_json = resp.json()
                        err_msg = err_json.get("error", {}).get("message") or err_json.get("message") or err_msg
                    except Exception:
                        pass
                    raise RuntimeError(f"XAI247 HTTP {resp.status_code}: {err_msg}")

                data = resp.json()
                choices = data.get("choices", [])
                if choices and isinstance(choices, list):
                    first_choice = choices[0]
                    msg = first_choice.get("message", {})
                    return msg.get("content") or ""
                return ""
        except Exception as e:
            logger.exception("Lỗi khi gọi XAI247 chat:")
            raise RuntimeError(f"XAI247 chat lỗi (model: {model_id}): {e}") from e


_meta_provider = MetaAIProvider()
_xai247_provider = XAI247Provider()
_PROVIDERS: dict[str, AIProvider] = {
    "gemini": GeminiProvider(),
    "openai": OpenAIProvider(),
    "ollama": OllamaProvider(),
    "meta": _meta_provider,
    "llama": _meta_provider,
    "xai247": _xai247_provider,
}


def get_ai_status() -> dict[str, Any]:
    """Kiểm tra trạng thái kết nối của các provider AI (Gemini, OpenAI, Ollama, Meta AI, XAI247)."""
    import httpx

    ollama_info: dict[str, Any] = {"running": False, "host": settings.OLLAMA_HOST, "models": []}
    try:
        r = httpx.get(f"{settings.OLLAMA_HOST}/api/tags", timeout=2.0)
        if r.status_code == 200:
            ollama_info["running"] = True
            models = r.json().get("models", [])
            ollama_info["models"] = [m.get("name", "") for m in models]
    except Exception:
        ollama_info["running"] = False

    meta_info: dict[str, Any] = {
        "configured": bool(settings.META_API_KEY),
        "url": settings.META_API_URL,
        "default_model": settings.META_MODEL or "llama-3.3-70b-instruct",
        "key_url": "https://dev.meta.ai/api-keys?team_id=1163733282980704&project_id=2093527077961508",
        "models": [],
    }
    if settings.META_API_KEY:
        try:
            r_meta = httpx.get(
                f"{settings.META_API_URL.strip().rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.META_API_KEY}"},
                timeout=3.0,
            )
            if r_meta.status_code == 200:
                m_list = r_meta.json().get("data", [])
                meta_info["models"] = [m.get("id", "") for m in m_list if m.get("id")]
        except Exception:
            pass

    xai247_info: dict[str, Any] = {
        "configured": bool(settings.XAI247_API_KEY),
        "url": settings.XAI247_API_URL,
        "default_model": settings.XAI247_MODEL or "deepseek-v4.1-flash",
        "key_url": "https://xai247.xyz/dashboard/keys",
        "models": [],
    }
    if settings.XAI247_API_KEY:
        try:
            r_xai = httpx.get(
                f"{settings.XAI247_API_URL.strip().rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.XAI247_API_KEY}"},
                timeout=3.0,
            )
            if r_xai.status_code == 200:
                m_list = r_xai.json().get("data", [])
                xai247_info["models"] = [m.get("id", "") for m in m_list if m.get("id")]
        except Exception:
            pass
    if not xai247_info["models"]:
        xai247_info["models"] = ["deepseek-v4.1-flash", "claude-3-5-sonnet", "gpt-4o", "deepseek-r1"]

    return {
        "gemini": {"configured": bool(settings.GEMINI_API_KEY)},
        "openai": {"configured": bool(settings.OPENAI_API_KEY)},
        "ollama": ollama_info,
        "meta": meta_info,
        "xai247": xai247_info,
    }


def get_provider(name: str) -> AIProvider:
    """Factory: trả provider theo tên, raise ValueError khi không có."""
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise ValueError(f"Provider không tồn tại: {name}. Chọn một trong {list(_PROVIDERS)}")
    return provider


def repair_json(text: str) -> tuple[dict[str, Any] | None, str]:
    """Fallback: model trả text thừa — trích JSON block đầu tiên.

    Returns:
        (json_dict hoặc None, raw_text)
    """
    if not text:
        return None, text
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0)), text
            except json.JSONDecodeError:
                pass
    return None, text


def analyze_with_provider(prompt: str, provider_name: str, options: AIOptions,
                          system_prompt: str) -> tuple[str, int]:
    """Gọi provider + đo duration (ms) + fallback khi 1 provider lỗi.

    Returns:
        (response_text, duration_ms)  — response là JSON hoặcplanation text.

    Raises:
        RuntimeError: khi tất cả providers thất bại.
    """
    start = time.perf_counter()
    primary = get_provider(provider_name)
    if not (options.api_key or primary.is_available()):
        logger.warning("Provider %s chưa cấu hình API key", provider_name)
        raise RuntimeError(
            f"Provider '{provider_name}' chưa cấu hình API key. "
            f"Vui lòng nhập API key trong Cài Đặt hoặc lấy key tại https://xai247.xyz"
        )
    try:
        text = primary.analyze(prompt, system_prompt, options)
        ms = int((time.perf_counter() - start) * 1000)
        return text, ms
    except Exception:
        logger.exception("Provider %s lỗi, fallback %s", provider_name, primary.name)
        raise
