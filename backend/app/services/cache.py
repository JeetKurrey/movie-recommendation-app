"""
Zero-infrastructure TTL cache.

The PRD explicitly calls for `functools.lru_cache` / diskcache-style caching
to stay inside the Gemini and OMDb free-tier rate limits without needing
Redis. This is a small async-safe TTL+LRU cache that works for a single
backend process (fine for the MVP scale described in the PRD — swap for
Redis later if you go multi-instance).
"""
import asyncio
import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    def __init__(self, max_entries: int = 2000):
        self._max_entries = max_entries
        self._store: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._store[key]
                return None
            # Refresh LRU order on access.
            self._store.move_to_end(key)
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + ttl_seconds, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


def make_cache_key(*parts: Any) -> str:
    return "|".join(str(p).strip().lower() for p in parts)
