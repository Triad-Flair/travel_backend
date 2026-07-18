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


@celery_app.task(name="app.workers.tasks.check_completed_trips", bind=True, max_retries=3)
def check_completed_trips(self):
    """Confirmed live: a real captured payment sat with Transfer: -- on
    Razorpay indefinitely — nothing anywhere ever called complete_trip
    (or execute_agency_payout) automatically; both were reachable only via
    manual admin action nobody was taking. This finds every CONFIRMED
    plan/package whose trip has actually ended and completes it. The
    agency's full payout already goes out at booking confirmation (see
    services/payments.py::_release_agency_payout_for_group) — this task
    marks the trip COMPLETED and acts only as a safety net, retrying the
    payout for any payment that somehow never went out at confirmation
    time. Self-limiting: once complete_trip flips status to COMPLETED, the
    same trip no longer matches this query on the next run."""
    async def _task():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.group import Group
        from app.models.package import Package
        from app.models.plan import Plan
        from app.services.payments import complete_trip

        now = datetime.now(UTC)
        async with AsyncSessionLocal() as db:
            plan_groups = await db.execute(
                select(Group.id)
                .join(Plan, Plan.id == Group.plan_id)
                .where(Plan.status == "CONFIRMED", Plan.end_date <= now)
            )
            package_groups = await db.execute(
                select(Group.id)
                .join(Package, Package.id == Group.package_id)
                .where(Package.status == "CONFIRMED", Package.end_date <= now)
            )
            group_ids = {row[0] for row in plan_groups.all()} | {row[0] for row in package_groups.all()}

            for group_id in group_ids:
                try:
                    await complete_trip(db, group_id)
                    await db.commit()
                except Exception:
                    logger.exception("Auto trip completion failed for group %s", group_id)
                    await db.rollback()

            logger.info("Trip completion job checked %d group(s) at %s", len(group_ids), now)

    try:
        _run_async(_task())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="app.workers.tasks.reconcile_stuck_group_confirmations", bind=True, max_retries=3)
def reconcile_stuck_group_confirmations(self):
    """Safety net: confirmed live that a group can occasionally reach its
    required captured-payment count without _finalize_capture's real-time
    path flipping the plan/package to CONFIRMED and releasing the agency's
    payout — root cause not conclusively pinned down (re-running the identical
    check against the same data afterward always finds it correctly
    confirmable, so this reads as a one-off transaction-timing issue
    rather than a standing logic bug). Scans every CONFIRMING plan/package
    and re-runs check_and_confirm_group, which is a no-op for anything
    genuinely still short of travelers."""
    async def _task():
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.group import Group
        from app.models.package import Package
        from app.models.plan import Plan
        from app.services.payments import check_and_confirm_group

        async with AsyncSessionLocal() as db:
            plan_groups = await db.execute(
                select(Group).join(Plan, Plan.id == Group.plan_id).where(Plan.status == "CONFIRMING")
            )
            package_groups = await db.execute(
                select(Group).join(Package, Package.id == Group.package_id).where(Package.status == "CONFIRMING")
            )
            groups = [*plan_groups.scalars().all(), *package_groups.scalars().all()]

            confirmed = 0
            for group in groups:
                try:
                    if await check_and_confirm_group(db, group):
                        confirmed += 1
                    await db.commit()
                except Exception:
                    logger.exception("Group confirmation reconciliation failed for group %s", group.id)
                    await db.rollback()

            logger.info("Group confirmation reconciliation checked %d group(s), confirmed %d", len(groups), confirmed)

    try:
        _run_async(_task())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


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


# ── Email automation hooks (PRD section 5) ───────────────────────────────────
# Each task re-opens its own DB session (Celery workers don't share the
# FastAPI request's session) and re-fetches whatever it needs by id, matching
# the pattern already used above (check_expired_plans etc).

@celery_app.task(name="app.workers.tasks.send_registration_email", bind=True, max_retries=3)
def send_registration_email_task(self, user_id: str, verification_token: str):
    async def _task():
        from app.database import AsyncSessionLocal
        from app.lib.email import send_verification_email
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            user = await db.get(User, user_id)
            if not user:
                # Most likely the caller's transaction hasn't committed yet —
                # retry rather than silently dropping the email.
                raise LookupError(f"User {user_id} not found (yet)")
            if not user.email:
                return
            await send_verification_email(
                user.email, user.display_name or user.username or "Traveler", verification_token
            )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_registration_email failed for user %s: %s", user_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.send_transactional_invoice_email", bind=True, max_retries=3)
def send_transactional_invoice_email_task(self, payment_id: str):
    async def _task():
        from app.config import settings
        from app.database import AsyncSessionLocal
        from app.lib.email import send_payment_receipt_email
        from app.models.payment import Payment
        from app.models.user import User
        from app.services import invoices as inv_svc

        async with AsyncSessionLocal() as db:
            payment = await db.get(Payment, payment_id)
            if not payment:
                raise LookupError(f"Payment {payment_id} not found (yet)")
            traveler = await db.get(User, payment.user_id)
            if not traveler:
                raise LookupError(f"User {payment.user_id} not found (yet)")
            if not traveler.email:
                return
            # Idempotent — re-running this after the inline call in
            # _send_capture_notifications either finds both PDFs already
            # generated (no-op) or fills the gap if that transaction hadn't
            # committed yet when this task started.
            invoice = await inv_svc.ensure_invoice_pdfs(db, payment)
            ctx = await inv_svc._trip_context(db, payment)
            trip = ctx["plan"] or ctx["package"]
            if not trip:
                return
            await send_payment_receipt_email(
                traveler.email,
                traveler.display_name or traveler.username or "Traveler",
                invoice.invoice_number,
                trip.title,
                f"{settings.frontend_url}/dashboard/invoices/{payment.id}",
                int(payment.amount or 0),
                pdf_bytes=invoice.user_pdf_data,
            )
            await db.commit()

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_transactional_invoice_email failed for payment %s: %s", payment_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.send_bid_alert_email", bind=True, max_retries=3)
def send_bid_alert_email_task(self, offer_id: str):
    async def _task():
        from app.database import AsyncSessionLocal
        from app.lib.email import send_offer_notification_email
        from app.models.agency import Agency
        from app.models.offer import Offer
        from app.models.plan import Plan
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            offer = await db.get(Offer, offer_id)
            if not offer:
                raise LookupError(f"Offer {offer_id} not found (yet)")
            plan = await db.get(Plan, offer.plan_id)
            if not plan:
                raise LookupError(f"Plan {offer.plan_id} not found")
            agency = await db.get(Agency, offer.agency_id)
            if not agency:
                raise LookupError(f"Agency {offer.agency_id} not found")
            creator = await db.get(User, plan.creator_id)
            if not creator or not creator.email:
                return
            await send_offer_notification_email(creator.email, plan.title, agency.name)

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_bid_alert_email failed for offer %s: %s", offer_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.send_dispute_alert_email", bind=True, max_retries=3)
def send_dispute_alert_email_task(self, dispute_id: str, event: str):
    async def _task():
        from app.config import settings
        from app.database import AsyncSessionLocal
        from app.lib.email import send_dispute_alert_email
        from app.models.payment import Dispute, Payment

        if not settings.platform_admin_email:
            logger.warning("PLATFORM_ADMIN_EMAIL not configured — dropping dispute alert for %s", dispute_id)
            return

        async with AsyncSessionLocal() as db:
            dispute = await db.get(Dispute, dispute_id)
            if not dispute:
                raise LookupError(f"Dispute {dispute_id} not found (yet)")
            payment = await db.get(Payment, dispute.payment_id)
            if not payment:
                return
            await send_dispute_alert_email(
                settings.platform_admin_email,
                event,
                dispute.status,
                payment.id,
                int(payment.amount or 0),
                bool(payment.payout_frozen),
            )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_dispute_alert_email failed for dispute %s: %s", dispute_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.send_review_alert_email", bind=True, max_retries=3)
def send_review_alert_email_task(self, review_id: str):
    async def _task():
        from app.database import AsyncSessionLocal
        from app.lib.email import send_review_alert_email
        from app.models.agency import Agency
        from app.models.social import Review
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            review = await db.get(Review, review_id)
            if not review:
                raise LookupError(f"Review {review_id} not found (yet)")
            reviewer = await db.get(User, review.reviewer_id)
            reviewer_name = (reviewer.display_name or reviewer.username or "Someone") if reviewer else "Someone"

            if review.review_type == "agency" and review.target_agency_id:
                agency = await db.get(Agency, review.target_agency_id)
                if not agency or not agency.owner_id:
                    return
                owner = await db.get(User, agency.owner_id)
                if not owner or not owner.email:
                    return
                await send_review_alert_email(
                    owner.email, owner.display_name or owner.username or agency.name,
                    reviewer_name, float(review.overall_rating or 0), review.comment,
                )
            elif review.target_user_id:
                target = await db.get(User, review.target_user_id)
                if not target or not target.email:
                    return
                await send_review_alert_email(
                    target.email, target.display_name or target.username or "there",
                    reviewer_name, float(review.overall_rating or 0), review.comment,
                )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_review_alert_email failed for review %s: %s", review_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.send_compliance_approval_email", bind=True, max_retries=3)
def send_compliance_approval_email_task(self, agency_id: str):
    async def _task():
        from app.database import AsyncSessionLocal
        from app.lib.email import send_compliance_approval_email
        from app.models.agency import Agency
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            agency = await db.get(Agency, agency_id)
            if not agency:
                raise LookupError(f"Agency {agency_id} not found (yet)")
            if not agency.owner_id:
                return
            owner = await db.get(User, agency.owner_id)
            if not owner or not owner.email:
                return
            await send_compliance_approval_email(
                owner.email, owner.display_name or owner.username or agency.name, agency.name
            )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_compliance_approval_email failed for agency %s: %s", agency_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.send_package_expiry_warning_email", bind=True, max_retries=3)
def send_package_expiry_warning_email_task(self, package_id: str):
    async def _task():
        from app.config import settings
        from app.database import AsyncSessionLocal
        from app.lib.email import send_package_expiry_warning_email
        from app.models.agency import Agency
        from app.models.package import Package
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            package = await db.get(Package, package_id)
            if not package or not package.expires_at:
                return
            agency = await db.get(Agency, package.agency_id)
            if not agency or not agency.owner_id:
                return
            owner = await db.get(User, agency.owner_id)
            if not owner or not owner.email:
                return
            await send_package_expiry_warning_email(
                owner.email,
                owner.display_name or owner.username or agency.name,
                package.title,
                package.expires_at.date().isoformat(),
                f"{settings.frontend_url}/agency/packages/{package.id}",
            )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_package_expiry_warning_email failed for package %s: %s", package_id, exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="app.workers.tasks.check_package_expiry_warnings", bind=True, max_retries=3)
def check_package_expiry_warnings(self, warning_window_days: int = 3):
    """Beat-scheduled (see app/celery_app.py) — finds OPEN packages expiring
    within warning_window_days that haven't been warned about yet, marks
    them, and fans out one send_package_expiry_warning_email task per
    package. Lifecycle Hook (PRD section 5)."""
    async def _task():
        from datetime import timedelta

        from sqlalchemy import select, update

        from app.database import AsyncSessionLocal
        from app.models.package import Package

        now = datetime.now(UTC)
        cutoff = now + timedelta(days=warning_window_days)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Package.id).where(
                    Package.status == "OPEN",
                    Package.expires_at.isnot(None),
                    Package.expires_at <= cutoff,
                    Package.expires_at > now,
                    Package.expiry_warning_sent_at.is_(None),
                )
            )
            package_ids = [row[0] for row in result.all()]
            if not package_ids:
                return

            await db.execute(
                update(Package)
                .where(Package.id.in_(package_ids))
                .values(expiry_warning_sent_at=now)
            )
            await db.commit()

        for package_id in package_ids:
            send_package_expiry_warning_email_task.delay(package_id)

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("check_package_expiry_warnings failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="app.workers.tasks.send_group_chat_verification_email", bind=True, max_retries=3)
def send_group_chat_verification_email_task(self, user_id: str, group_id: str):
    async def _task():
        from app.config import settings
        from app.database import AsyncSessionLocal
        from app.lib.email import send_group_chat_verification_email
        from app.models.group import Group
        from app.models.package import Package
        from app.models.plan import Plan
        from app.models.user import User

        async with AsyncSessionLocal() as db:
            group = await db.get(Group, group_id)
            if not group:
                # Newly created by accept_offer/book_package — the caller's
                # transaction may not have committed yet. Retry.
                raise LookupError(f"Group {group_id} not found (yet)")
            user = await db.get(User, user_id)
            if not user or not user.email:
                return
            trip_title = None
            if group.plan_id:
                plan = await db.get(Plan, group.plan_id)
                trip_title = plan.title if plan else None
            if not trip_title and group.package_id:
                package = await db.get(Package, group.package_id)
                trip_title = package.title if package else None
            if not trip_title:
                return
            await send_group_chat_verification_email(
                user.email,
                user.display_name or user.username or "there",
                trip_title,
                f"{settings.frontend_url}/dashboard/groups/{group.id}/chat",
            )

    try:
        _run_async(_task())
    except Exception as exc:
        logger.error("send_group_chat_verification_email failed for user %s / group %s: %s", user_id, group_id, exc)
        raise self.retry(exc=exc, countdown=30)
