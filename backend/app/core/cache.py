"""
app/core/cache.py
Redis cache (Upstash-compatible) with a lightweight in-memory fallback.
Set REDIS_URL in .env to enable Redis; leave blank for local dev without Redis.
"""
import json
import time
from typing import Any, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_memory_store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)


class CacheBackend:
    async def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError


class MemoryCache(CacheBackend):
    async def get(self, key: str) -> Optional[Any]:
        entry = _memory_store.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del _memory_store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        _memory_store[key] = (value, time.time() + ttl_seconds)

    async def delete(self, key: str) -> None:
        _memory_store.pop(key, None)


class RedisCache(CacheBackend):
    def __init__(self, redis_client):
        self._r = redis_client

    async def get(self, key: str) -> Optional[Any]:
        raw = await self._r.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        await self._r.set(key, json.dumps(value), ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._r.delete(key)


_cache_instance: Optional[CacheBackend] = None


async def get_cache() -> CacheBackend:
    global _cache_instance
    if _cache_instance is not None:
        return _cache_instance

    settings = get_settings()
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            await r.ping()
            _cache_instance = RedisCache(r)
            log.info("cache.redis.connected")
        except Exception as e:
            log.warning("cache.redis.fallback", error=str(e))
            _cache_instance = MemoryCache()
    else:
        log.info("cache.memory.enabled")
        _cache_instance = MemoryCache()

    return _cache_instance
