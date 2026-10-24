"""Rate limiting với SlowApi."""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

settings = get_settings()

# Key theo IP remote address
limiter = Limiter(key_func=get_remote_address)

RATE_LIMIT = settings.RATE_LIMIT


def default_limit() -> str:
    """Rate limit mặc định từ env."""
    return RATE_LIMIT
