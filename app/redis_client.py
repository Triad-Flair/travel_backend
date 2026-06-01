import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis | None:
    global _redis
    if _redis is None:
        try:
            _redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                max_connections=50,
            )
            await _redis.ping()
            logger.info("Redis connected: %s", settings.redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable, caching disabled: %s", exc)
            _redis = None
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


class RedisCache:
    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None

    async def _get_client(self) -> aioredis.Redis | None:
        if self._client is None:
            self._client = await get_redis()
        return self._client

    async def get(self, key: str) -> str | None:
        client = await self._get_client()
        if client is None:
            return None
        try:
            return await client.get(key)
        except Exception as exc:
            logger.warning("Redis GET failed for key %s: %s", key, exc)
            return None

    async def set(self, key: str, value: str, ttl: int = 300) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            await client.setex(key, ttl, value)
            return True
        except Exception as exc:
            logger.warning("Redis SET failed for key %s: %s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            await client.delete(key)
            return True
        except Exception as exc:
            logger.warning("Redis DEL failed for key %s: %s", key, exc)
            return False

    async def delete_pattern(self, pattern: str) -> int:
        client = await self._get_client()
        if client is None:
            return 0
        try:
            count = 0
            async for key in client.scan_iter(match=pattern, count=100):
                await client.delete(key)
                count += 1
            return count
        except Exception as exc:
            logger.warning("Redis DEL pattern failed for %s: %s", pattern, exc)
            return 0

    async def incr(self, key: str, amount: int = 1) -> int | None:
        client = await self._get_client()
        if client is None:
            return None
        try:
            return await client.incrby(key, amount)
        except Exception:
            return None

    async def expire(self, key: str, ttl: int) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            return bool(await client.expire(key, ttl))
        except Exception:
            return False

    async def publish(self, channel: str, message: str) -> int:
        client = await self._get_client()
        if client is None:
            return 0
        try:
            return await client.publish(channel, message)
        except Exception as exc:
            logger.warning("Redis PUBLISH failed: %s", exc)
            return 0

    async def hset(self, name: str, mapping: dict[str, Any]) -> int:
        client = await self._get_client()
        if client is None:
            return 0
        try:
            return await client.hset(name, mapping=mapping)
        except Exception:
            return 0

    async def hget(self, name: str, key: str) -> str | None:
        client = await self._get_client()
        if client is None:
            return None
        try:
            return await client.hget(name, key)
        except Exception:
            return None

    async def hgetall(self, name: str) -> dict:
        client = await self._get_client()
        if client is None:
            return {}
        try:
            return await client.hgetall(name)
        except Exception:
            return {}


cache = RedisCache()
