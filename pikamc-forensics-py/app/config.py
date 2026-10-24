"""Cấu hình ứng dụng - load từ biến môi trường (.env) qua pydantic-settings."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Toàn bộ secrets/cấu hình đi qua env, KHÔNG hardcode."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    VERSION: str = "1.0.0"
    START_TIME: float = 0.0  # Set lúc app startup (dùng cho uptime)

    # Database
    DATABASE_URL: str = "sqlite:///./data/forensics.db"

    # PikaMC API (chỉ GET)
    PIKAMC_API_URL: str = "https://hub.pikamc.vn/api"
    PIKAMC_API_KEY: str = "your_key_here"

    # AI providers
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OLLAMA_HOST: str = "http://localhost:11434"
    META_API_KEY: str = ""
    META_API_URL: str = "https://api.meta.ai/v1"
    META_MODEL: str = "llama-3.3-70b-instruct"
    XAI247_API_KEY: str = ""
    XAI247_API_URL: str = "https://xai247.xyz/v1"
    XAI247_MODEL: str = "claude-3-5-sonnet"

    # Security
    API_SECRET: str = "your_frontend_auth_secret"
    RATE_LIMIT: str = "100/minute"
    CACHE_TTL: int = 300


@lru_cache
def get_settings() -> Settings:
    """Singleton Settings để tái sử dụng trong toàn app."""
    return Settings()


# Ngưỡng phát hiện gian lận (dùng trong metrics_service.classify)
THRESHOLDS: dict[str, dict[str, float]] = {
    "xray":       {"suspicious": 25, "confirmed": 50, "critical": 100},
    "fastbreak":  {"suspicious": 1,  "confirmed": 10},
    "combat_kd":  {"suspicious": 10, "confirmed": 10},
    "movement":   {"suspicious": 15, "confirmed": 30},
    "bot_hours":  {"suspicious": 100, "confirmed": 200},
}
