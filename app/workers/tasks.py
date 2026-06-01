import asyncio
import logging
from datetime import UTC, datetime

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.workers.tasks.check_expired_plans", bind=True, max_retries=3)
def check_expired_plans(self):
    async def _task():
        from sqlalchemy import select, update
        from app.database import AsyncSessionLocal
        from app.models.plan import Plan
        from app.models.enums import PlanStatus

        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Plan)
                .where(
                    Plan.status == PlanStatus.OPEN,
                    Plan.expires_at <= now,
                )
                .values(status=PlanStatus.EXPIRED)
            )
            await db.commit()
            logger.info("Expired plans job completed at %s", now)

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("check_expired_plans failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="app.workers.tasks.check_expired_offers", bind=True, max_retries=3)
def check_expired_offers(self):
    async def _task():
        from sqlalchemy import update
        from app.database import AsyncSessionLocal
        from app.models.offer import Offer
        from app.models.enums import OfferStatus

        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Offer)
                .where(
                    Offer.status.in_([OfferStatus.PENDING, OfferStatus.COUNTERED]),
                    Offer.expires_at <= now,
                )
                .values(status=OfferStatus.WITHDRAWN)
            )
            await db.commit()
            logger.info("Expired offers job completed at %s", now)

    try:
        _run_async(_task())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="app.workers.tasks.expire_loyalty_points", bind=True, max_retries=3)
def expire_loyalty_points(self):
    async def _task():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.loyalty import LoyaltyPointsLedger
        from app.models.enums import LoyaltyAction
        import uuid

        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(LoyaltyPointsLedger)
                .where(
                    LoyaltyPointsLedger.expires_at <= now,
                    LoyaltyPointsLedger.points > 0,
                )
            )
            expiring = result.scalars().all()

            for entry in expiring:
                from sqlalchemy import func
                balance = await db.scalar(
                    select(func.sum(LoyaltyPointsLedger.points))
                    .where(LoyaltyPointsLedger.user_id == entry.user_id)
                ) or 0

                if balance > 0 and entry.points > 0:
                    expire_points = min(entry.points, balance)
                    expiry = LoyaltyPointsLedger(
                        id=str(uuid.uuid4()),
                        user_id=entry.user_id,
                        action=LoyaltyAction.EXPIRY,
                        points=-expire_points,
                        balance_after=balance - expire_points,
                        description="Points expired",
                        reference_id=entry.id,
                    )
                    db.add(expiry)
                    entry.expires_at = None  # Mark as processed

            await db.commit()

    try:
        _run_async(_task())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.workers.tasks.check_payment_windows", bind=True, max_retries=3)
def check_payment_windows(self):
    async def _task():
        from sqlalchemy import select, update
        from app.database import AsyncSessionLocal
        from app.models.group import Group

        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Group)
                .where(
                    Group.payment_window_open == True,
                    Group.payment_window_closes_at <= now,
                )
                .values(payment_window_open=False)
            )
            await db.commit()

    try:
        _run_async(_task())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.notify_direct_message", bind=True, max_retries=2)
def notify_direct_message(self, recipient_id: str, sender_name: str, content_preview: str):
    """Send a WhatsApp push when a DM arrives — runs off the hot request path."""
    async def _task():
        from app.database import AsyncSessionLocal
        from app.models.user import User
        from app.config import settings

        async with AsyncSessionLocal() as db:
            user = await db.get(User, recipient_id)
            if not user:
                return
            phone = getattr(user, "phone", None)
            if phone and settings.msg91_auth_key and settings.msg91_template_id:
                send_whatsapp_notification.delay(
                    phone=phone,
                    template_id=settings.msg91_template_id,
                    params=[sender_name, content_preview],
                )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("notify_direct_message failed for %s: %s", recipient_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.track_package_view")
def track_package_view(package_id: str):
    """Increment a Redis view counter — fire-and-forget, never blocks the API."""
    import redis as _redis
    from app.config import settings

    try:
        client = _redis.from_url(settings.redis_url, decode_responses=True)
        client.incr(f"pkg:views:{package_id}")
        client.close()
    except Exception as exc:
        logger.warning("track_package_view failed for %s: %s", package_id, exc)


@celery_app.task(name="app.workers.tasks.send_whatsapp_notification")
def send_whatsapp_notification(phone: str, template_id: str, params: list[str]):
    import httpx
    from app.config import settings

    if not settings.msg91_auth_key:
        return

    try:
        resp = httpx.post(
            "https://api.msg91.com/api/v5/flow/",
            headers={"authkey": settings.msg91_auth_key, "Content-Type": "application/json"},
            json={
                "template_id": template_id,
                "short_url": "0",
                "recipients": [{"mobiles": phone, "params": params}],
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.error("WhatsApp notification failed to %s: %s", phone, exc)
