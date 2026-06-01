import functools
import json
import logging
from collections.abc import Callable
from typing import Any

from app.redis_client import cache

logger = logging.getLogger(__name__)

# TTL constants (seconds)
TTL_SHORT = 60          # 1 min - real-time data
TTL_MEDIUM = 300        # 5 min - list pages
TTL_LONG = 900          # 15 min - detail pages
TTL_EXTRA_LONG = 3600   # 1 hour - static/rarely-changing data


def make_cache_key(*parts: Any) -> str:
    return ":".join(str(p) for p in parts if p is not None)


async def get_cached(key: str) -> Any | None:
    raw = await cache.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def set_cached(key: str, value: Any, ttl: int = TTL_MEDIUM) -> None:
    try:
        serialized = json.dumps(value, default=str)
        await cache.set(key, serialized, ttl)
    except Exception as exc:
        logger.warning("Cache set failed for key %s: %s", key, exc)


async def invalidate(key: str) -> None:
    await cache.delete(key)


async def invalidate_pattern(pattern: str) -> None:
    await cache.delete_pattern(pattern)


def cached(key_prefix: str, ttl: int = TTL_MEDIUM):
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = make_cache_key(key_prefix, *args, *kwargs.values())
            cached_val = await get_cached(cache_key)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            if result is not None:
                await set_cached(cache_key, result, ttl)
            return result
        return wrapper
    return decorator


# Domain-specific cache key builders
class CacheKeys:
    @staticmethod
    def plan_detail(plan_id: str) -> str:
        return f"plan:detail:{plan_id}"

    @staticmethod
    def plan_list(page: int, filters_hash: str) -> str:
        return f"plan:list:{page}:{filters_hash}"

    @staticmethod
    def package_detail(pkg_id: str) -> str:
        return f"package:detail:{pkg_id}"

    @staticmethod
    def package_list(page: int, filters_hash: str) -> str:
        return f"package:list:{page}:{filters_hash}"

    @staticmethod
    def agency_detail(agency_id: str) -> str:
        return f"agency:detail:{agency_id}"

    @staticmethod
    def agency_by_slug(slug: str) -> str:
        return f"agency:slug:{slug}"

    @staticmethod
    def user_profile(username: str) -> str:
        return f"user:profile:{username}"

    @staticmethod
    def discover_feed(page: int, filters_hash: str) -> str:
        return f"discover:feed:{page}:{filters_hash}"

    @staticmethod
    def trending(page: int) -> str:
        return f"discover:trending:{page}"

    @staticmethod
    def group_detail(group_id: str) -> str:
        return f"group:detail:{group_id}"

    @staticmethod
    def plan_offers(plan_id: str) -> str:
        return f"plan:offers:{plan_id}"

    @staticmethod
    def follow_state(follower_id: str, subject_id: str) -> str:
        return f"social:follow:{follower_id}:{subject_id}"

    @staticmethod
    def loyalty_balance(user_id: str) -> str:
        return f"loyalty:balance:{user_id}"

    @staticmethod
    def wallet_balance(user_id: str) -> str:
        return f"wallet:balance:{user_id}"

    @staticmethod
    def notifications(user_id: str, page: int) -> str:
        return f"notifications:{user_id}:{page}"
