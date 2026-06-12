import logging

import socketio

from app.config import settings
from app.core.security import decode_access_token
from app.exceptions import InvalidTokenError, TokenExpiredError

logger = logging.getLogger(__name__)

# Redis Pub/Sub manager — enables cross-process room routing when running
# multiple uvicorn/gunicorn workers. Falls back to in-memory if Redis is absent.
_redis_mgr: socketio.AsyncRedisManager | None = None
try:
    _redis_mgr = socketio.AsyncRedisManager(settings.redis_url, channel="sio")
    logger.info("Socket.IO using Redis Pub/Sub manager")
except Exception as _exc:
    logger.warning("Socket.IO Redis manager unavailable, falling back to in-memory: %s", _exc)

sio = socketio.AsyncServer(
    client_manager=_redis_mgr,
    async_mode="asgi",
    cors_allowed_origins=settings.cors_origins,
    logger=False,
    engineio_logger=False,
)

# Map: sid → user_id
_sid_user: dict[str, str] = {}


def _extract_value(data: object, key: str) -> str | None:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


# ── Auth ──────────────────────────────────────────────────────────────────────
@sio.event
async def connect(sid: str, environ: dict, auth: dict | None):
    token = (auth or {}).get("token") if auth else None
    if not token:
        # Try query string fallback
        qs = environ.get("QUERY_STRING", "")
        for part in qs.split("&"):
            if part.startswith("token="):
                token = part[6:]
                break

    if not token:
        logger.warning("Socket.IO rejected unauthenticated connection %s", sid)
        return False  # reject

    try:
        payload = decode_access_token(token)
        user_id = payload["sub"]
        _sid_user[sid] = user_id
        # Auto-join personal room
        await sio.enter_room(sid, f"user:{user_id}")
        logger.debug("Socket.IO connected: sid=%s user=%s", sid, user_id)
    except (InvalidTokenError, TokenExpiredError) as exc:
        logger.warning("Socket.IO auth failed for %s: %s", sid, exc)
        return False


@sio.event
async def disconnect(sid: str):
    user_id = _sid_user.pop(sid, None)
    logger.debug("Socket.IO disconnected: sid=%s user=%s", sid, user_id)


# ── Room subscriptions ────────────────────────────────────────────────────────
async def _group_subscribe_impl(sid: str, data: object):
    group_id = _extract_value(data, "groupId")
    if group_id:
        await sio.enter_room(sid, f"group:{group_id}")


async def _group_unsubscribe_impl(sid: str, data: object):
    group_id = _extract_value(data, "groupId")
    if group_id:
        await sio.leave_room(sid, f"group:{group_id}")


async def _direct_subscribe_impl(sid: str, data: object):
    conv_id = _extract_value(data, "conversationId")
    if conv_id:
        await sio.enter_room(sid, f"direct:{conv_id}")


async def _direct_unsubscribe_impl(sid: str, data: object):
    conv_id = _extract_value(data, "conversationId")
    if conv_id:
        await sio.leave_room(sid, f"direct:{conv_id}")


@sio.on("group:subscribe")
async def group_subscribe_colon(sid: str, data: object):
    await _group_subscribe_impl(sid, data)


@sio.on("group:unsubscribe")
async def group_unsubscribe_colon(sid: str, data: object):
    await _group_unsubscribe_impl(sid, data)


@sio.on("direct:subscribe")
async def direct_subscribe_colon(sid: str, data: object):
    await _direct_subscribe_impl(sid, data)


@sio.on("direct:unsubscribe")
async def direct_unsubscribe_colon(sid: str, data: object):
    await _direct_unsubscribe_impl(sid, data)


# Backward-compatible snake_case aliases
@sio.event
async def group_subscribe(sid: str, data: object):
    await _group_subscribe_impl(sid, data)


@sio.event
async def group_unsubscribe(sid: str, data: object):
    await _group_unsubscribe_impl(sid, data)


@sio.event
async def direct_subscribe(sid: str, data: object):
    await _direct_subscribe_impl(sid, data)


@sio.event
async def direct_unsubscribe(sid: str, data: object):
    await _direct_unsubscribe_impl(sid, data)


# ── Typing indicators ─────────────────────────────────────────────────────────
async def _group_typing_impl(sid: str, data: object):
    payload = data if isinstance(data, dict) else {}
    group_id = _extract_value(payload, "groupId")
    user_id = _sid_user.get(sid)
    if group_id and user_id:
        await sio.emit(
            "chat:typing",
            {
                "groupId": group_id,
                "userId": user_id,
                "isTyping": bool(payload.get("isTyping", True)),
            },
            room=f"group:{group_id}",
            skip_sid=sid,
        )


async def _direct_typing_impl(sid: str, data: object):
    payload = data if isinstance(data, dict) else {}
    conv_id = _extract_value(payload, "conversationId")
    user_id = _sid_user.get(sid)
    if conv_id and user_id:
        await sio.emit(
            "direct:typing",
            {
                "conversationId": conv_id,
                "userId": user_id,
                "isTyping": bool(payload.get("isTyping", True)),
            },
            room=f"direct:{conv_id}",
            skip_sid=sid,
        )


@sio.on("group:typing")
async def group_typing_colon(sid: str, data: object):
    await _group_typing_impl(sid, data)


@sio.on("direct:typing")
async def direct_typing_colon(sid: str, data: object):
    await _direct_typing_impl(sid, data)


# Backward-compatible snake_case aliases
@sio.event
async def group_typing(sid: str, data: object):
    await _group_typing_impl(sid, data)


@sio.event
async def direct_typing(sid: str, data: object):
    await _direct_typing_impl(sid, data)


# ── Emit helpers (called from services) ──────────────────────────────────────
async def emit_to_group(group_id: str, event: str, data: dict) -> None:
    await sio.emit(event, data, room=f"group:{group_id}")


async def emit_to_user(user_id: str, event: str, data: dict) -> None:
    await sio.emit(event, data, room=f"user:{user_id}")


async def emit_to_conversation(conv_id: str, event: str, data: dict) -> None:
    await sio.emit(event, data, room=f"direct:{conv_id}")


# ASGI app — mounted at /socket.io in main.py
socket_asgi_app = socketio.ASGIApp(sio, socketio_path="socket.io")
