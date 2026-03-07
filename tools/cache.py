"""
Simple thread-safe in-memory TTL cache for expensive tool calls.

Usage:
    from tools.cache import ttl_cache

    @ttl_cache(ttl=3600)
    def my_expensive_function(arg1, arg2):
        ...
"""

import functools
import threading
import time


class _TTLCache:
    """Thread-safe dict with per-entry expiry."""

    def __init__(self):
        self._store: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> tuple[bool, object]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False, None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return False, None
            return True, value

    def set(self, key: str, value: object, ttl: float) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_global_cache = _TTLCache()


def ttl_cache(ttl: float = 3600):
    """Decorator that caches function results for `ttl` seconds.

    The cache key is built from all positional and keyword arguments.
    Only JSON-serialisable arguments are supported as keys.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__module__}.{fn.__qualname__}:{args!r}:{sorted(kwargs.items())!r}"
            hit, cached = _global_cache.get(key)
            if hit:
                return cached
            result = fn(*args, **kwargs)
            # Only cache successful results
            if isinstance(result, dict) and result.get("status") != "error":
                _global_cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator
