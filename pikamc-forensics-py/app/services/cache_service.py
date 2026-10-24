"""In-memory TTL cache, thread-safe với threading.Lock."""
import functools
import threading
import time
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}  # key -> (expire_ts, value)


def cache_get(key: str) -> Any | None:
    """Lấy value từ cache nếu còn hạn TTL, ngược lại xóa và trả None."""
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expire, value = entry
        if expire < now:
            _store.pop(key, None)
            return None
        return value


def cache_set(key: str, value: Any, ttl: int) -> None:
    """Ghi value vào cache với TTL (giây)."""
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def cache_clear() -> int:
    """Xóa toàn bộ cache. Trả về số entry đã xóa."""
    with _lock:
        n = len(_store)
        _store.clear()
        return n


def cached(ttl: int = 300) -> Callable:
    """Decorator: cache kết quả hàm async/sync theo key từ *args."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{fn.__module__}.{fn.__qualname__}:{args}:{sorted(kwargs.items())}"
            hit = cache_get(key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            cache_set(key, result, ttl)
            return result

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{fn.__module__}.{fn.__qualname__}:{args}:{sorted(kwargs.items())}"
            hit = cache_get(key)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            cache_set(key, result, ttl)
            return result

        # Chọn wrapper theo kiểu hàm
        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper

    return decorator
