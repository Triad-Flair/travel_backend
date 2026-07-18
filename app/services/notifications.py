import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.social import Notification, ProfileView
from app.models.user import User
from app.schemas.notifications import NotificationResponse, ProfileViewResponse

logger = logging.getLogger(__name__)


def _maybe_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return None


def _notif_to_response(n: Notification) -> NotificationResponse:
    metadata = None
    if n.extra_data:
        if isinstance(n.extra_data, dict):
            metadata = n.extra_data
        else:
            try:
                metadata = json.loads(str(n.extra_data))
            except Exception:
                metadata = None
    read_at = _maybe_dt(n.read_at)
    return NotificationResponse(
        id=n.id,
        type=n.type,
        title=n.title,
        body=n.body,
        href=n.href,
        metadata=metadata if isinstance(metadata, dict) else None,
        read=bool(read_at),
        read_at=read_at,
        created_at=n.created_at,
    )


async def create_notification(
    db: AsyncSession,
    user_id: str,
    type: str,
    title: str,
    body: str,
    href: str | None = None,
    metadata: dict | None = None,
) -> Notification:
    """Central place to create an in-app Notification row — before this
    existed, every event type except "new follower" and a chat compliance
    reminder had no notification path at all (purchases, referrals, wallet
    changes, reviews, group-join, expiry/reminders all only ever sent, at
    best, a one-off transactional email). Also pushes a live
    "notification:created" socket event so the bell dropdown updates
    without a page reload — the frontend already listened for this event
    (use-live-notifications.ts), but nothing on the backend ever emitted
    it, so live push silently never worked.

    The socket push is best-effort: a Redis/socket hiccup must never take
    down whatever business transaction (payment capture, referral credit,
    etc.) triggered this notification, so it's caught and logged, not
    re-raised."""
    notif_id = str(uuid.uuid4())
    created_at = datetime.now(UTC)
    notif = Notification(
        id=notif_id,
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        href=href,
        extra_data=metadata,
    )
    db.add(notif)
    await db.flush()

    try:
        from app.websockets.socketio_server import emit_to_user
        # Built directly rather than via _notif_to_response(notif) — that
        # reads notif.created_at, which depends on the flush actually
        # having run against a real database (server_default=func.now()).
        # This push is best-effort and ephemeral, so it shouldn't depend on
        # that at all; every value it needs is already known here.
        await emit_to_user(user_id, "notification:created", {
            "id": notif_id,
            "type": type,
            "title": title,
            "body": body,
            "href": href,
            "metadata": metadata,
            "read": False,
            "readAt": None,
            "createdAt": created_at.isoformat(),
        })
    except Exception:
        logger.exception("Failed to push live notification %s to user %s", notif_id, user_id)

    return notif


async def list_notifications(
    db: AsyncSession, user_id: str, limit: int
) -> list[NotificationResponse]:
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return [_notif_to_response(n) for n in result.scalars().all()]


async def mark_notification_read(db: AsyncSession, notif_id: str, user_id: str) -> dict:
    notif = await db.scalar(
        select(Notification).where(
            Notification.id == notif_id,
            Notification.user_id == user_id,
        )
    )
    if not notif:
        return {"id": notif_id, "readAt": None, "read": False}

    now = datetime.now(UTC)
    notif.read_at = now
    await db.flush()
    return {"id": notif.id, "readAt": now.isoformat(), "read": True}


async def mark_all_read(db: AsyncSession, user_id: str) -> dict:
    now = datetime.now(UTC)
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    updated_count = result.rowcount or 0
    return {"updatedCount": int(updated_count), "readAt": now.isoformat()}


async def get_profile_views(
    db: AsyncSession, user_id: str, limit: int
) -> list[ProfileViewResponse]:
    rows = await db.execute(
        select(ProfileView, User)
        .join(User, User.id == ProfileView.viewer_user_id)
        .where(ProfileView.target_owner_user_id == user_id)
        .order_by(ProfileView.created_at.desc())
        .limit(limit)
    )

    items: list[ProfileViewResponse] = []
    for view, viewer in rows.all():
        if view.target_type == "AGENCY":
            target_handle = view.target_agency_id or "agency"
            target_name = "Agency profile"
            target_type = "agency"
        else:
            target_handle = view.target_user_id or "profile"
            target_name = "Traveler profile"
            target_type = "traveler"

        items.append(
            ProfileViewResponse(
                id=view.id,
                created_at=view.created_at,
                target_type=target_type,
                target_handle=target_handle,
                target_name=target_name,
                viewer={
                    "id": viewer.id,
                    "handle": viewer.username or viewer.id,
                    "fullName": viewer.display_name or viewer.username or "Traveler",
                    "avatarUrl": viewer.avatar_url,
                },
            )
        )

    return items
