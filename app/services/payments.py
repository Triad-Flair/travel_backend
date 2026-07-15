import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError, PaymentError
from app.lib.email import (
    send_agency_booking_invoice_email,
    send_agency_payout_update_email,
)
from app.lib.razorpay_client import capture_payment, create_order, create_transfer, verify_signature
from app.models.agency import Agency, AgencyBankAccount, AgencyTransaction, AgencyWallet
from app.models.group import Group, GroupMember
from app.models.loyalty import LoyaltyPointsLedger, ReferralWallet
from app.models.offer import Offer
from app.models.package import Package
from app.models.payment import Dispute, Payment, PromoCodeUsage, PromotionalDiscount
from app.models.plan import Plan
from app.models.user import User
from app.schemas.payments import (
    AgencyWalletSummary,
    AgencyWalletTransaction,
    CreateDisputeRequest,
    CreateOrderRequest,
    DisputeResponse,
    GroupPaymentOrderResponse,
    GroupPaymentStateResponse,
    CreatePromoCodeRequest,
    MockCaptureRequest,
    PaymentRecordResponse,
    PromoCodeResponse,
    ValidatePromoRequest,
    ValidatePromoResponse,
    VerifyPaymentRequest,
)
from app.services import invoices as inv_svc

logger = logging.getLogger(__name__)

PLATFORM_FEE_PAISE = 0  # no separate platform fee is charged to travelers — platform revenue is commission-only, deducted from the agency's payout
FEE_GST_RATE = 0.18  # GST is charged on the package price itself (18%), paid by the traveler on top — it's strictly between traveler and platform and never touches the agency's payout
COMMISSION_RATE = 0.10
TRANCHE_1_RATIO = 0.45
TRANCHE_2_RATIO = 0.55

ACTIVE_MEMBER_STATUSES = {"APPROVED", "COMMITTED"}


def _tranche_amount(agency_net: int, tranche: str) -> int:
    ratio = TRANCHE_2_RATIO if tranche == "tranche2" else TRANCHE_1_RATIO
    return int(round(agency_net * ratio))


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _compute_breakdown(trip_amount: int) -> dict:
    fee_gst = int(round(trip_amount * FEE_GST_RATE))
    commission = int(round(trip_amount * COMMISSION_RATE))
    total = trip_amount + PLATFORM_FEE_PAISE + fee_gst
    return {
        "tripAmount": int(trip_amount),
        "platformFeeAmount": int(PLATFORM_FEE_PAISE),
        "feeGstAmount": int(fee_gst),
        "commissionAmount": int(commission),
        "agencyNetAmount": int(trip_amount - commission),
        "totalAmount": int(total),
    }


def _payment_to_response(payment: Payment) -> PaymentRecordResponse:
    return PaymentRecordResponse(
        id=payment.id,
        user_id=payment.user_id,
        group_id=payment.group_id,
        amount=int(payment.amount),
        currency=payment.currency,
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment.razorpay_payment_id,
        status=payment.status,
        escrow_status=payment.escrow_status,
        tranche1_released=bool(payment.tranche1_released),
        tranche2_released=bool(payment.tranche2_released),
        payout_frozen=bool(payment.payout_frozen),
        points_redeemed=int(payment.points_redeemed or 0),
        wallet_amount_used=int(payment.wallet_amount_used or 0),
        promo_code=payment.promo_code,
        promo_discount_amount=int(payment.promo_discount_amount or 0),
        trip_amount=int(payment.trip_amount or 0),
        platform_fee_amount=int(payment.platform_fee_amount or 0),
        fee_gst_amount=int(payment.fee_gst_amount or 0),
        commission_amount=int(payment.commission_amount or 0),
        created_at=payment.created_at.isoformat(),
        updated_at=payment.updated_at.isoformat(),
    )


async def _send_capture_notifications(db: AsyncSession, payment: Payment) -> None:
    invoice = await inv_svc.ensure_invoice_pdfs(db, payment)
    ctx = await inv_svc._trip_context(db, payment)
    trip = ctx["plan"] or ctx["package"]
    agency = ctx["agency"]

    traveler = await db.scalar(select(User).where(User.id == payment.user_id))
    if traveler and traveler.email and trip:
        # PRD trigger: send_transactional_invoice_email — routed through
        # Celery (see app/workers/tasks.py) rather than a direct await. The
        # task re-runs ensure_invoice_pdfs itself (idempotent) rather than
        # trusting this transaction has committed by the time it executes.
        from app.workers.tasks import send_transactional_invoice_email_task
        send_transactional_invoice_email_task.delay(payment.id)

    if agency and agency.owner_id and trip:
        owner = await db.scalar(select(User).where(User.id == agency.owner_id))
        if owner and owner.email:
            await send_agency_booking_invoice_email(
                owner.email,
                owner.display_name or owner.username or agency.name,
                agency.name,
                invoice.invoice_number.replace("TSU", "TSA"),
                trip.title,
                f"{settings.frontend_url}/agency/invoices/{payment.id}",
                int(payment.amount or 0),
                pdf_bytes=invoice.agency_pdf_data,
            )


async def _send_payout_notification(db: AsyncSession, payment: Payment, tranche: str) -> None:
    invoice = await inv_svc._ensure_invoice(db, payment)
    ctx = await inv_svc._trip_context(db, payment)
    agency = ctx["agency"]
    if not agency or not agency.owner_id:
        return

    owner = await db.scalar(select(User).where(User.id == agency.owner_id))
    if not owner or not owner.email:
        return

    agency_net = int((payment.trip_amount or 0) - (payment.commission_amount or 0))
    tranche_label = "Final settlement" if tranche == "tranche2" else "Advance payout"
    released_amount = _tranche_amount(agency_net, tranche)

    await send_agency_payout_update_email(
        owner.email,
        owner.display_name or owner.username or agency.name,
        agency.name,
        invoice.invoice_number.replace("TSU", "TSA"),
        tranche_label,
        payment.escrow_status,
        f"{settings.frontend_url}/agency/invoices/{payment.id}",
        released_amount,
    )


async def _get_payment_context(db: AsyncSession, group_id: str, user_id: str) -> dict:
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")

    membership = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    if not membership:
        raise ForbiddenError("You are not a member of this group")
    if membership.status not in {"INTERESTED", "APPROVED", "COMMITTED"}:
        raise ForbiddenError("Membership is not active")

    plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id)) if group.plan_id else None
    package = await db.scalar(select(Package).where(Package.id == group.package_id)) if group.package_id else None

    offer = None
    agency = None
    payment_source = None
    if plan:
        if plan.confirmed_offer_id:
            offer = await db.scalar(select(Offer).where(Offer.id == plan.confirmed_offer_id))
        if not offer:
            offer = await db.scalar(
                select(Offer)
                .where(Offer.plan_id == plan.id, Offer.status.in_(["ACCEPTED", "COUNTERED", "PENDING"]))
                .order_by(Offer.updated_at.desc())
            )
        if not offer:
            raise BadRequestError("Payment opens after an agency offer is available")
        agency = await db.scalar(select(Agency).where(Agency.id == offer.agency_id))
        payment_source = "PLAN_OFFER"
        trip_amount = int(offer.price_per_person) * 100
    elif package:
        agency = await db.scalar(select(Agency).where(Agency.id == package.agency_id))
        payment_source = "PACKAGE"
        trip_amount = int(package.price_per_person) * 100
    else:
        raise BadRequestError("Group has neither a plan nor a package")

    if not agency:
        raise NotFoundError("Agency")

    member_rows = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.status.in_(ACTIVE_MEMBER_STATUSES),
        )
    )
    active_members = member_rows.scalars().all()
    traveler_count = max(1, len(active_members))

    committed_count = sum(1 for m in active_members if m.status == "COMMITTED")

    payment = await db.scalar(
        select(Payment)
        .where(Payment.group_id == group_id, Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )

    loyalty_points = await db.scalar(
        select(func.coalesce(func.sum(LoyaltyPointsLedger.points), 0)).where(
            LoyaltyPointsLedger.user_id == user_id,
            LoyaltyPointsLedger.expired == False,
        )
    )
    loyalty_points = int(loyalty_points or 0)

    wallet = await db.scalar(select(ReferralWallet).where(ReferralWallet.user_id == user_id))
    wallet_rupees = int(wallet.balance or 0) if wallet else 0

    breakdown = _compute_breakdown(trip_amount)
    max_redeemable_points = max(0, min(loyalty_points, int(trip_amount * 0.20 / 100)))
    max_wallet_usable_rupees = max(0, min(wallet_rupees, int(trip_amount * 0.20 / 100)))

    if payment and payment.status == "CAPTURED":
        checkout_mode = "captured"
    elif settings.razorpay_key_id and settings.razorpay_key_secret:
        checkout_mode = "razorpay"
    else:
        checkout_mode = "mock"

    return {
        "group": group,
        "membership": membership,
        "plan": plan,
        "package": package,
        "offer": offer,
        "agency": agency,
        "payment_source": payment_source,
        "breakdown": breakdown,
        "trip_amount": trip_amount,
        "payment": payment,
        "committed_count": committed_count,
        "traveler_count": traveler_count,
        "loyalty_points": loyalty_points,
        "max_redeemable_points": max_redeemable_points,
        "wallet_rupees": wallet_rupees,
        "max_wallet_usable_rupees": max_wallet_usable_rupees,
        "checkout_mode": checkout_mode,
    }


async def _finalize_capture(db: AsyncSession, payment: Payment) -> None:
    payment.status = "CAPTURED"
    payment.paid_at = datetime.now(UTC)

    if payment.promo_code:
        promo = await db.scalar(
            select(PromotionalDiscount).where(
                func.upper(PromotionalDiscount.code) == payment.promo_code.upper()
            )
        )
        if promo:
            db.add(
                PromoCodeUsage(
                    id=str(uuid.uuid4()),
                    promo_id=promo.id,
                    user_id=payment.user_id,
                    payment_id=payment.id,
                    discount_applied=int(payment.promo_discount_amount or 0),
                    used_at=datetime.utcnow(),
                )
            )

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == payment.group_id,
            GroupMember.user_id == payment.user_id,
        )
    )
    if member and member.status != "COMMITTED":
        member.status = "COMMITTED"
        member.committed_at = datetime.now(UTC)

    group = await db.scalar(select(Group).where(Group.id == payment.group_id))
    if not group:
        return

    members_rows = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == payment.group_id,
            GroupMember.status.in_(ACTIVE_MEMBER_STATUSES),
        )
    )
    members = members_rows.scalars().all()
    member_user_ids = [m.user_id for m in members]
    if not member_user_ids:
        return

    captured_count = await db.scalar(
        select(func.count(Payment.id)).where(
            Payment.group_id == payment.group_id,
            Payment.user_id.in_(member_user_ids),
            Payment.status == "CAPTURED",
        )
    ) or 0

    if group.plan_id:
        plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id))
        if plan:
            min_required = max(1, int(plan.group_size_min or 1))
            if int(captured_count) >= min_required and int(captured_count) == len(member_user_ids):
                plan.status = "CONFIRMED"
                plan.confirmed_at = datetime.now(UTC)
            elif plan.status == "OPEN":
                plan.status = "CONFIRMING"

    if group.package_id:
        package = await db.scalar(select(Package).where(Package.id == group.package_id))
        if package:
            min_required = max(1, int(package.group_size_min or 1))
            if int(captured_count) >= min_required and int(captured_count) == len(member_user_ids):
                package.status = "CONFIRMED"
            elif package.status == "OPEN":
                package.status = "CONFIRMING"

    await inv_svc._ensure_invoice(db, payment)
    await db.flush()
    await _send_capture_notifications(db, payment)


async def list_my_payments(
    db: AsyncSession,
    user_id: str,
    page: int,
    page_size: int,
) -> tuple[list[PaymentRecordResponse], int]:
    total = await db.scalar(select(func.count(Payment.id)).where(Payment.user_id == user_id)) or 0
    rows = await db.execute(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [_payment_to_response(p) for p in rows.scalars().all()], int(total)


async def get_group_payment_state(db: AsyncSession, group_id: str, user_id: str) -> GroupPaymentStateResponse:
    ctx = await _get_payment_context(db, group_id, user_id)

    payment = ctx["payment"]
    breakdown = dict(ctx["breakdown"])
    if payment:
        breakdown["pointsRedeemed"] = int(payment.points_redeemed or 0)
        breakdown["pointsDiscount"] = int(payment.points_redeemed or 0) * 100
        breakdown["walletAmountUsed"] = int(payment.wallet_amount_used or 0)
        breakdown["walletDiscount"] = int(payment.wallet_amount_used or 0) * 100

    plan_payload = None
    if ctx["plan"]:
        plan = ctx["plan"]
        plan_payload = {
            "id": plan.id,
            "title": plan.title,
            "slug": plan.slug,
            "status": plan.status,
        }

    package_payload = None
    if ctx["package"]:
        package = ctx["package"]
        package_payload = {
            "id": package.id,
            "title": package.title,
            "slug": package.slug,
            "status": package.status,
        }

    offer_payload = None
    if ctx["offer"]:
        offer = ctx["offer"]
        offer_payload = {
            "id": offer.id,
            "agencyName": ctx["agency"].name,
            "pricePerPerson": int(offer.price_per_person),
        }

    return GroupPaymentStateResponse(
        group_id=group_id,
        agency_name=ctx["agency"].name,
        payment_source=ctx["payment_source"],
        plan=plan_payload,
        package=package_payload,
        offer=offer_payload,
        payment=_payment_to_response(payment) if payment else None,
        amount=int(payment.amount) if payment else int(ctx["breakdown"]["totalAmount"]),
        breakdown=breakdown,
        loyalty={
            "availablePoints": ctx["loyalty_points"],
            "maxRedeemablePoints": ctx["max_redeemable_points"],
            "maxDiscountPaise": ctx["max_redeemable_points"] * 100,
            "pointValueInr": 1,
        },
        wallet={
            "availableBalanceRupees": ctx["wallet_rupees"],
            "maxUsableRupees": ctx["max_wallet_usable_rupees"],
            "autoApply": False,
        },
        currency="INR",
        committed_count=int(ctx["committed_count"]),
        traveler_count=int(ctx["traveler_count"]),
        checkout_mode=ctx["checkout_mode"],
        razorpay_key_id=settings.razorpay_key_id or None,
    )


async def create_payment_order(
    db: AsyncSession,
    group_id: str,
    user_id: str,
    req: CreateOrderRequest,
) -> GroupPaymentOrderResponse:
    ctx = await _get_payment_context(db, group_id, user_id)

    existing = ctx["payment"]
    if existing and existing.status == "CAPTURED":
        return GroupPaymentOrderResponse(
            payment=_payment_to_response(existing),
            amount=int(existing.amount),
            breakdown=ctx["breakdown"],
            payment_source=ctx["payment_source"],
            currency=existing.currency,
            checkout_mode="captured",
            razorpay_key_id=settings.razorpay_key_id or None,
            description=f"{ctx['plan'].title if ctx['plan'] else ctx['package'].title} · {ctx['agency'].name}",
        )

    points = max(0, int(req.points_to_redeem or 0))
    points = min(points, int(ctx["max_redeemable_points"]))

    wallet_rupees = max(0, int(req.wallet_amount_to_use or 0))
    wallet_rupees = min(wallet_rupees, int(ctx["max_wallet_usable_rupees"]))

    points_discount = points * 100
    wallet_discount = wallet_rupees * 100
    gross_amount = int(ctx["breakdown"]["totalAmount"])

    promo_code = (req.promo_code or "").strip().upper() or None
    promo_discount = 0
    if promo_code:
        promo, promo_discount, _ = await _resolve_promo_discount(db, promo_code, user_id, gross_amount)
        if not promo:
            promo_code = None

    final_amount = max(100, gross_amount - points_discount - wallet_discount - promo_discount)

    checkout_mode = "razorpay" if settings.razorpay_key_id and settings.razorpay_key_secret else "mock"

    payment = existing
    if payment and payment.status in {"PENDING", "FAILED"}:
        payment.amount = final_amount
        payment.points_redeemed = points
        payment.wallet_amount_used = wallet_rupees
        payment.promo_code = promo_code
        payment.promo_discount_amount = promo_discount
        payment.trip_amount = int(ctx["breakdown"]["tripAmount"])
        payment.platform_fee_amount = int(ctx["breakdown"]["platformFeeAmount"])
        payment.fee_gst_amount = int(ctx["breakdown"]["feeGstAmount"])
        payment.commission_amount = int(ctx["breakdown"]["commissionAmount"])
        payment.status = "PENDING"
    else:
        payment = Payment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            group_id=group_id,
            agency_id=ctx["agency"].id,
            plan_id=ctx["plan"].id if ctx["plan"] else None,
            package_id=ctx["package"].id if ctx["package"] else None,
            amount=final_amount,
            currency="INR",
            status="PENDING",
            escrow_status="HELD",
            trip_amount=int(ctx["breakdown"]["tripAmount"]),
            platform_fee_amount=int(ctx["breakdown"]["platformFeeAmount"]),
            fee_gst_amount=int(ctx["breakdown"]["feeGstAmount"]),
            commission_amount=int(ctx["breakdown"]["commissionAmount"]),
            source=ctx["payment_source"],
            points_redeemed=points,
            wallet_amount_used=wallet_rupees,
            promo_code=promo_code,
            promo_discount_amount=promo_discount,
        )
        db.add(payment)

    description = f"{ctx['plan'].title if ctx['plan'] else ctx['package'].title} · {ctx['agency'].name}"
    if checkout_mode == "razorpay":
        try:
            order = create_order(
                final_amount,
                receipt=f"grp_{group_id[:12]}",
                notes={"groupId": group_id, "userId": user_id, "paymentId": payment.id},
            )
            payment.razorpay_order_id = order.get("id")
        except PaymentError:
            checkout_mode = "mock"

    if checkout_mode == "mock" and not payment.razorpay_order_id:
        payment.razorpay_order_id = f"order_mock_{payment.id.replace('-', '')[:20]}"

    await db.flush()
    # payment.updated_at has onupdate=func.now() — see the identical comment
    # in services/offers.py::counter_offer for why this refresh is needed.
    await db.refresh(payment)

    breakdown = dict(ctx["breakdown"])
    breakdown["pointsRedeemed"] = points
    breakdown["pointsDiscount"] = points_discount
    breakdown["walletAmountUsed"] = wallet_rupees
    breakdown["walletDiscount"] = wallet_discount
    breakdown["promoCode"] = promo_code
    breakdown["promoDiscount"] = promo_discount

    return GroupPaymentOrderResponse(
        payment=_payment_to_response(payment),
        amount=final_amount,
        breakdown=breakdown,
        payment_source=ctx["payment_source"],
        currency=payment.currency,
        checkout_mode=checkout_mode,
        razorpay_key_id=settings.razorpay_key_id or None,
        description=description,
    )


async def _resolve_promo_discount(
    db: AsyncSession, code: str, user_id: str, gross_amount: int
) -> tuple[PromotionalDiscount | None, int, str]:
    """Look up a promo code and compute its discount against gross_amount (paise).

    Returns (promo, discount_paise, message) — promo is None (discount 0) for
    any code that doesn't apply, with message explaining why.
    """
    normalized = (code or "").strip().upper()
    if not normalized:
        return None, 0, "Enter a promo code"

    promo = await db.scalar(
        select(PromotionalDiscount).where(func.upper(PromotionalDiscount.code) == normalized)
    )
    if not promo or not promo.is_active:
        return None, 0, "Invalid promo code"

    if promo.expires_at and promo.expires_at <= datetime.now(UTC):
        return None, 0, "This promo code has expired"

    if promo.min_order_amount_paise and gross_amount < promo.min_order_amount_paise:
        return None, 0, f"Minimum order amount for this code is ₹{promo.min_order_amount_paise // 100}"

    if promo.usage_limit is not None:
        total_uses = await db.scalar(
            select(func.count(PromoCodeUsage.id)).where(PromoCodeUsage.promo_id == promo.id)
        ) or 0
        if int(total_uses) >= promo.usage_limit:
            return None, 0, "This promo code has reached its usage limit"

    if promo.per_user_limit is not None:
        user_uses = await db.scalar(
            select(func.count(PromoCodeUsage.id)).where(
                PromoCodeUsage.promo_id == promo.id,
                PromoCodeUsage.user_id == user_id,
            )
        ) or 0
        if int(user_uses) >= promo.per_user_limit:
            return None, 0, "You have already used this promo code"

    if promo.discount_type == "PERCENTAGE":
        discount = int(gross_amount * promo.discount_value / 100)
    else:
        discount = int(promo.discount_value)

    if promo.max_discount_paise is not None:
        discount = min(discount, promo.max_discount_paise)
    discount = max(0, min(discount, gross_amount))

    return promo, discount, "Promo code applied"


async def validate_promo(
    db: AsyncSession,
    req: ValidatePromoRequest,
    user_id: str,
) -> ValidatePromoResponse:
    ctx = await _get_payment_context(db, req.group_id, user_id)
    gross_amount = int(ctx["breakdown"]["totalAmount"])
    promo, discount_paise, message = await _resolve_promo_discount(db, req.code, user_id, gross_amount)
    if not promo:
        return ValidatePromoResponse(
            valid=False,
            discount_type=None,
            discount_value=None,
            discount_paise=None,
            message=message,
        )
    return ValidatePromoResponse(
        valid=True,
        discount_type=promo.discount_type,
        discount_value=int(promo.discount_value),
        discount_paise=discount_paise,
        message=message,
    )


async def _promo_to_response(db: AsyncSession, promo: PromotionalDiscount) -> PromoCodeResponse:
    times_used = await db.scalar(
        select(func.count(PromoCodeUsage.id)).where(PromoCodeUsage.promo_id == promo.id)
    ) or 0
    # discount_applied per usage is the exact cost this promo code has
    # imposed on the platform — trip_amount/commission (what the agency is
    # owed) and fee_gst_amount (what's remitted to the government) are
    # computed from the undiscounted breakdown and never reduced by a
    # promo, so every rupee of discount here is a rupee the platform funds
    # from its own margin/reserves rather than the traveler's payment.
    total_discount_given = await db.scalar(
        select(func.coalesce(func.sum(PromoCodeUsage.discount_applied), 0)).where(
            PromoCodeUsage.promo_id == promo.id
        )
    ) or 0
    return PromoCodeResponse(
        id=promo.id,
        code=promo.code,
        is_active=bool(promo.is_active),
        description=promo.description,
        discount_type=promo.discount_type,
        discount_value=int(promo.discount_value),
        max_discount_paise=promo.max_discount_paise,
        min_order_amount_paise=promo.min_order_amount_paise,
        usage_limit=promo.usage_limit,
        per_user_limit=promo.per_user_limit,
        expires_at=promo.expires_at,
        times_used=int(times_used),
        total_discount_given_paise=int(total_discount_given),
        created_at=promo.created_at,
    )


async def create_promo_code(db: AsyncSession, req: CreatePromoCodeRequest) -> PromoCodeResponse:
    code = req.code.strip().upper()
    if not code:
        raise BadRequestError("Promo code is required")
    if req.discount_type not in {"PERCENTAGE", "FLAT"}:
        raise BadRequestError("discountType must be PERCENTAGE or FLAT")
    if req.discount_value <= 0:
        raise BadRequestError("discountValue must be greater than 0")
    if req.discount_type == "PERCENTAGE" and req.discount_value > 100:
        raise BadRequestError("A percentage discount cannot exceed 100")

    existing = await db.scalar(
        select(PromotionalDiscount).where(func.upper(PromotionalDiscount.code) == code)
    )
    if existing:
        raise BadRequestError(f"Promo code '{code}' already exists")

    promo = PromotionalDiscount(
        id=str(uuid.uuid4()),
        code=code,
        is_active=True,
        description=req.description,
        discount_type=req.discount_type,
        discount_value=req.discount_value,
        max_discount_paise=req.max_discount_paise,
        min_order_amount_paise=req.min_order_amount_paise,
        usage_limit=req.usage_limit,
        per_user_limit=req.per_user_limit,
        expires_at=req.expires_at,
    )
    db.add(promo)
    await db.flush()
    return await _promo_to_response(db, promo)


async def list_promo_codes(db: AsyncSession) -> list[PromoCodeResponse]:
    rows = await db.execute(select(PromotionalDiscount).order_by(PromotionalDiscount.created_at.desc()))
    promos = rows.scalars().all()
    return [await _promo_to_response(db, promo) for promo in promos]


async def set_promo_code_active(db: AsyncSession, promo_id: str, is_active: bool) -> PromoCodeResponse:
    promo = await db.scalar(select(PromotionalDiscount).where(PromotionalDiscount.id == promo_id))
    if not promo:
        raise NotFoundError("Promo code")
    promo.is_active = is_active
    await db.flush()
    return await _promo_to_response(db, promo)


async def verify_payment(db: AsyncSession, req: VerifyPaymentRequest, user_id: str) -> PaymentRecordResponse:
    payment = await db.scalar(select(Payment).where(Payment.id == req.payment_id))
    if not payment:
        raise NotFoundError("Payment")
    if payment.user_id != user_id:
        raise ForbiddenError("You cannot verify this payment")

    if payment.status == "CAPTURED":
        return _payment_to_response(payment)

    if not payment.razorpay_order_id:
        raise BadRequestError("Payment order is missing")

    should_verify_signature = bool(settings.razorpay_key_secret)
    if should_verify_signature and not verify_signature(
        req.razorpay_order_id,
        req.razorpay_payment_id,
        req.razorpay_signature,
    ):
        raise PaymentError("Invalid payment signature")

    payment.razorpay_order_id = req.razorpay_order_id
    payment.razorpay_payment_id = req.razorpay_payment_id
    await _finalize_capture(db, payment)
    await db.flush()
    # payment.updated_at has onupdate=func.now() — see the identical comment
    # in services/offers.py::counter_offer for why this refresh is needed.
    await db.refresh(payment)

    return _payment_to_response(payment)


async def handle_hosted_checkout_callback(
    db: AsyncSession,
    razorpay_order_id: str | None,
    razorpay_payment_id: str | None,
    razorpay_signature: str | None,
) -> str:
    """Razorpay's hosted checkout page (form-POST redirect flow, not the JS
    overlay) sends the browser back here via a server-side POST with no auth
    session attached — the HMAC signature is the only trust boundary, same
    as the webhook. Always returns a redirect target; never raises, since
    there's no request originator left to show an error response to."""
    fallback_url = f"{settings.frontend_url}/dashboard/trips"

    if not razorpay_order_id or not razorpay_payment_id or not razorpay_signature:
        logger.warning("Hosted checkout callback missing required fields")
        return f"{fallback_url}?payment=failed"

    payment = await db.scalar(select(Payment).where(Payment.razorpay_order_id == razorpay_order_id))
    if not payment:
        logger.warning("Hosted checkout callback for unknown order_id=%s", razorpay_order_id)
        return f"{fallback_url}?payment=failed"

    checkout_url = f"{settings.frontend_url}/dashboard/groups/{payment.group_id}/checkout"

    if payment.status == "CAPTURED":
        # Idempotent — the webhook and this callback both fire for the same
        # payment, in no guaranteed order.
        return f"{checkout_url}?payment=success"

    if not verify_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        logger.error("Hosted checkout callback signature mismatch for payment %s", payment.id)
        return f"{checkout_url}?payment=failed"

    payment.razorpay_payment_id = razorpay_payment_id
    try:
        await _finalize_capture(db, payment)
        await db.flush()
    except Exception:
        # The signature is already verified above — Razorpay confirms the
        # money genuinely moved — so a failure here is our own bookkeeping
        # bug (invoice/notification/promo usage), not a failed payment. The
        # payment.captured webhook fires independently and will retry this
        # same finalization; there's nothing left in this request to show
        # the payer except a raw crash page, which is worse than a
        # momentarily-incomplete but honest "success". Roll back so the
        # partially-applied changes don't leave the session unusable for
        # the commit in get_db().
        logger.exception(
            "Post-capture finalization failed for payment %s after a verified Razorpay "
            "payment — the payment.captured webhook will retry; the payer's money is not at risk.",
            payment.id,
        )
        await db.rollback()

    return f"{checkout_url}?payment=success"


async def mock_capture(db: AsyncSession, req: MockCaptureRequest, user_id: str) -> PaymentRecordResponse:
    payment = await db.scalar(select(Payment).where(Payment.id == req.payment_id))
    if not payment:
        raise NotFoundError("Payment")
    if payment.user_id != user_id:
        raise ForbiddenError("You cannot capture this payment")

    if payment.status == "CAPTURED":
        return _payment_to_response(payment)

    if not payment.razorpay_payment_id:
        payment.razorpay_payment_id = f"pay_mock_{payment.id.replace('-', '')[:20]}"

    await _finalize_capture(db, payment)
    await db.flush()
    await db.refresh(payment)  # see comment in verify_payment re: expired onupdate column
    return _payment_to_response(payment)


def _dispute_to_response(dispute: Dispute) -> DisputeResponse:
    return DisputeResponse(
        id=dispute.id,
        payment_id=dispute.payment_id,
        reason=dispute.reason,
        status=dispute.status,
        source=dispute.source,
        razorpay_dispute_id=dispute.razorpay_dispute_id,
        created_at=dispute.created_at.isoformat(),
    )


async def create_dispute(db: AsyncSession, req: CreateDisputeRequest, user_id: str) -> DisputeResponse:
    payment = await db.scalar(select(Payment).where(Payment.id == req.payment_id))
    if not payment:
        raise NotFoundError("Payment")
    if payment.user_id != user_id:
        raise ForbiddenError("You cannot dispute this payment")

    # Scoped to source == CUSTOMER: a Razorpay chargeback (source ==
    # RAZORPAY_CHARGEBACK) can already exist on this payment without the
    # traveler ever filing anything — returning that record here would
    # mislabel a bank chargeback as their own support ticket.
    dispute = await db.scalar(
        select(Dispute).where(Dispute.payment_id == payment.id, Dispute.source == "CUSTOMER")
    )
    if dispute:
        return _dispute_to_response(dispute)

    dispute = Dispute(
        id=str(uuid.uuid4()),
        payment_id=payment.id,
        reason=req.reason,
        status="OPEN",
        source="CUSTOMER",
    )
    db.add(dispute)
    await db.flush()

    return _dispute_to_response(dispute)


async def get_agency_wallet_summary(db: AsyncSession, agency_id: str) -> AgencyWalletSummary:
    wallet = await db.scalar(select(AgencyWallet).where(AgencyWallet.agency_id == agency_id))
    if not wallet:
        wallet = AgencyWallet(id=str(uuid.uuid4()), agency_id=agency_id)
        db.add(wallet)
        await db.flush()

    return AgencyWalletSummary(
        pending_balance=int(wallet.pending_balance or 0),
        available_balance=int(wallet.available_balance or 0),
        total_earned=int(wallet.total_earned or 0),
        total_commission=int(wallet.total_commission or 0),
        security_deposit=int(wallet.security_deposit or 0),
        payout_mode=wallet.payout_mode or "TRUST",
    )


async def list_agency_wallet_transactions(db: AsyncSession, agency_id: str) -> list[AgencyWalletTransaction]:
    wallet = await db.scalar(select(AgencyWallet).where(AgencyWallet.agency_id == agency_id))
    if not wallet:
        return []

    rows = await db.execute(
        select(AgencyTransaction)
        .where(AgencyTransaction.wallet_id == wallet.id)
        .order_by(AgencyTransaction.created_at.desc())
        .limit(200)
    )

    return [
        AgencyWalletTransaction(
            id=tx.id,
            type=tx.type,
            amount=int(tx.amount),
            description=tx.description,
            group_id=tx.group_id,
            payment_id=tx.payment_id,
            razorpay_transfer_id=tx.razorpay_transfer_id,
            created_at=tx.created_at.isoformat(),
        )
        for tx in rows.scalars().all()
    ]


DISPUTE_EVENT_STATUS = {
    "payment.dispute.created": "OPEN",
    "payment.dispute.under_review": "UNDER_REVIEW",
    "payment.dispute.action_required": "ACTION_REQUIRED",
    "payment.dispute.won": "WON",
    "payment.dispute.lost": "LOST",
    "payment.dispute.closed": "CLOSED",
}
DISPUTE_STATUSES_FREEZE_PAYOUT = {"OPEN", "UNDER_REVIEW", "ACTION_REQUIRED"}
DISPUTE_STATUSES_UNFREEZE_PAYOUT = {"WON", "CLOSED"}


async def handle_razorpay_webhook(db: AsyncSession, event: str, payload: dict) -> None:
    """Dispatches every Razorpay event this account can emit. `payload` is
    the raw `data["payload"]` dict — each event category nests its entity
    under a different key (payment/order/dispute/invoice/payment_link), so
    extraction is each handler's own responsibility rather than done once
    up front for a shape that's only correct for payment.* events."""
    if event in ("payment.captured", "order.paid"):
        await _handle_payment_captured(db, payload)
    elif event == "payment.failed":
        await _handle_payment_failed(db, payload)
    elif event == "payment.authorized":
        await _handle_payment_authorized(db, payload)
    elif event in DISPUTE_EVENT_STATUS:
        await _handle_payment_dispute(db, event, payload)
    elif event.startswith("payment.downtime."):
        _handle_payment_downtime(event, payload)
    elif event in ("order.notification.delivered", "order.notification.failed"):
        # Informational — Razorpay's own reminder-email delivery status for
        # an unpaid order. No payment state to change.
        logger.info("Razorpay webhook %s received (informational, no action)", event)
    elif event.startswith("invoice.") or event.startswith("payment_link."):
        # Razorpay Invoicing and Payment Links are separate Razorpay
        # products this platform's checkout never creates (create_order is
        # the only order-creation path, via the Orders API) — these can only
        # fire if someone starts using those products from the same
        # account. Logged rather than silently dropped so that's visible if
        # it ever happens, instead of pretending to handle a flow that
        # doesn't exist here.
        logger.info("Razorpay webhook %s received but this product isn't used by the platform — no-op", event)
    else:
        logger.warning("Unhandled Razorpay webhook event: %s", event)


async def _handle_payment_captured(db: AsyncSession, payload: dict) -> None:
    payment_entity = payload.get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")
    if not order_id:
        return

    payment = await db.scalar(select(Payment).where(Payment.razorpay_order_id == order_id))
    if not payment:
        logger.warning("payment.captured/order.paid webhook for unknown order_id=%s", order_id)
        return
    if payment.status == "CAPTURED":
        # order.paid and payment.captured both fire for the same
        # transition — this makes handling either, or both, idempotent.
        return

    captured_amount = payment_entity.get("amount")
    if captured_amount is not None and int(captured_amount) != int(payment.amount):
        # The signature already proves this came from Razorpay, so the
        # money genuinely moved for this amount — record it rather than
        # drop it, but a mismatch points at a bug in our own price
        # computation (coupon/wallet edge case, a race on order creation)
        # that needs a human, not fraud to block the capture on.
        logger.error(
            "Amount mismatch on capture for payment %s: expected %s paise, Razorpay reports %s paise",
            payment.id, payment.amount, captured_amount,
        )

    payment.razorpay_payment_id = payment_entity.get("id") or payment.razorpay_payment_id
    await _finalize_capture(db, payment)
    await db.flush()


async def _handle_payment_failed(db: AsyncSession, payload: dict) -> None:
    payment_entity = payload.get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")
    if not order_id:
        return

    payment = await db.scalar(select(Payment).where(Payment.razorpay_order_id == order_id))
    if not payment or payment.status == "CAPTURED":
        # Out-of-order delivery (a late/retried failed event arriving after
        # a later capture already succeeded) must never downgrade a
        # successfully captured payment back to FAILED.
        return

    payment.status = "FAILED"
    await db.flush()


async def _handle_payment_authorized(db: AsyncSession, payload: dict) -> None:
    payment_entity = payload.get("payment", {}).get("entity", {})
    order_id = payment_entity.get("order_id")
    if not order_id:
        return

    payment = await db.scalar(select(Payment).where(Payment.razorpay_order_id == order_id))
    if not payment or payment.status == "CAPTURED":
        return

    razorpay_payment_id = payment_entity.get("id")
    payment.status = "AUTHORIZED"
    if razorpay_payment_id:
        payment.razorpay_payment_id = razorpay_payment_id
    await db.flush()

    # This checkout flow always wants immediate full capture (create_order
    # never sets payment_capture=0) — an AUTHORIZED payment almost always
    # means the Razorpay account's auto-capture setting is off for this
    # payment method. Capture explicitly rather than let the authorization
    # expire (Razorpay auto-voids/refunds an uncaptured authorization after
    # a few days), which would otherwise silently fail the traveler's
    # booking with no signal to us.
    if razorpay_payment_id:
        try:
            capture_payment(razorpay_payment_id, int(payment.amount))
        except PaymentError:
            logger.exception("Explicit capture failed for authorized payment %s", payment.id)
            # Left AUTHORIZED — a retried webhook, a later payment.captured
            # event, or manual admin action can still resolve this.


async def _handle_payment_dispute(db: AsyncSession, event: str, payload: dict) -> None:
    dispute_entity = payload.get("dispute", {}).get("entity", {})
    payment_entity = payload.get("payment", {}).get("entity", {})
    razorpay_dispute_id = dispute_entity.get("id")
    order_id = payment_entity.get("order_id")
    if not razorpay_dispute_id or not order_id:
        logger.warning("Malformed dispute webhook for event %s", event)
        return

    payment = await db.scalar(select(Payment).where(Payment.razorpay_order_id == order_id))
    if not payment:
        logger.warning("Dispute webhook %s for unknown order_id=%s", event, order_id)
        return

    new_status = DISPUTE_EVENT_STATUS[event]
    dispute = await db.scalar(select(Dispute).where(Dispute.razorpay_dispute_id == razorpay_dispute_id))
    if dispute:
        dispute.status = new_status
    else:
        dispute = Dispute(
            id=str(uuid.uuid4()),
            payment_id=payment.id,
            reason=dispute_entity.get("reason_code") or "razorpay_chargeback",
            status=new_status,
            source="RAZORPAY_CHARGEBACK",
            razorpay_dispute_id=razorpay_dispute_id,
        )
        db.add(dispute)

    # Freeze payouts the instant a chargeback opens — releasing tranche2 to
    # the agency while Razorpay might claw the funds back leaves the
    # platform holding the loss with no recourse from the agency. LOST stays
    # frozen: the funds are gone, and if a tranche was already paid out an
    # admin has to reconcile it manually — there's no automatic clawback
    # from the agency wallet here, deliberately, since debiting it
    # automatically could take a balance negative.
    if new_status in DISPUTE_STATUSES_FREEZE_PAYOUT:
        payment.payout_frozen = True
    elif new_status in DISPUTE_STATUSES_UNFREEZE_PAYOUT:
        payment.payout_frozen = False

    await db.flush()
    logger.warning("Razorpay dispute %s -> %s for payment %s", razorpay_dispute_id, new_status, payment.id)

    if settings.platform_admin_email:
        from app.workers.tasks import send_dispute_alert_email_task
        send_dispute_alert_email_task.delay(dispute.id, event)


def _handle_payment_downtime(event: str, payload: dict) -> None:
    # Informational — describes degraded availability of a payment method
    # (e.g. a bank's UPI being down), not tied to any single order/payment.
    # No order_id exists to correlate against, so there's no Payment row to
    # update; logged so it's visible to whoever watches application logs.
    downtime_entity = payload.get("payment", {}).get("downtime", {}).get("entity", {})
    logger.warning(
        "Razorpay downtime %s: id=%s method=%s status=%s",
        event, downtime_entity.get("id"), downtime_entity.get("method"), downtime_entity.get("status"),
    )


async def get_checkout_breakdown(
    db: AsyncSession,
    group_id: str,
    user_id: str,
    points_to_redeem: int | None = None,
    wallet_amount_to_use: int | None = None,
) -> dict:
    ctx = await _get_payment_context(db, group_id, user_id)
    points = max(0, int(points_to_redeem or 0))
    points = min(points, int(ctx["max_redeemable_points"]))
    wallet_rupees = max(0, int(wallet_amount_to_use or 0))
    wallet_rupees = min(wallet_rupees, int(ctx["max_wallet_usable_rupees"]))

    points_discount = points * 100
    wallet_discount = wallet_rupees * 100
    gross = int(ctx["breakdown"]["totalAmount"])
    final_amount = max(100, gross - points_discount - wallet_discount)

    breakdown = dict(ctx["breakdown"])
    breakdown["pointsRedeemed"] = points
    breakdown["pointsDiscount"] = points_discount
    breakdown["walletAmountUsed"] = wallet_rupees
    breakdown["walletDiscount"] = wallet_discount
    breakdown["finalAmount"] = final_amount
    return breakdown


async def list_disputes_for_agency(db: AsyncSession, agency_id: str) -> list[dict]:
    rows = await db.execute(
        select(Dispute)
        .join(Payment, Payment.id == Dispute.payment_id)
        .where(Payment.agency_id == agency_id)
        .order_by(Dispute.created_at.desc())
    )
    return [
        {
            "id": dispute.id,
            "paymentId": dispute.payment_id,
            "reason": dispute.reason,
            "status": dispute.status,
            "source": dispute.source,
            "razorpayDisputeId": dispute.razorpay_dispute_id,
            "createdAt": dispute.created_at.isoformat(),
        }
        for dispute in rows.scalars().all()
    ]


async def resolve_dispute(db: AsyncSession, dispute_id: str, resolution: str, notes: str | None = None) -> dict:
    dispute = await db.scalar(select(Dispute).where(Dispute.id == dispute_id))
    if not dispute:
        raise NotFoundError("Dispute")
    dispute.status = "RESOLVED"

    # An admin manually resolving is treated as authoritative — unfreeze any
    # payout this dispute was holding back, even for a RAZORPAY_CHARGEBACK
    # record (webhook events normally drive that lifecycle, but an admin
    # override here shouldn't leave a payment stuck frozen with no path out).
    payment = await db.scalar(select(Payment).where(Payment.id == dispute.payment_id))
    if payment and payment.payout_frozen:
        payment.payout_frozen = False

    await db.flush()
    return {
        "id": dispute.id,
        "paymentId": dispute.payment_id,
        "status": dispute.status,
        "resolution": resolution,
        "notes": notes,
        "resolvedAt": datetime.now(UTC).isoformat(),
    }


async def get_payment_tracking_map(db: AsyncSession, user_id: str) -> list[dict]:
    rows = await db.execute(
        select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc())
    )
    return [
        {
            "id": payment.id,
            "groupId": payment.group_id,
            "status": payment.status,
            "escrowStatus": payment.escrow_status,
            "amount": int(payment.amount),
            "currency": payment.currency,
            "paidAt": _iso(payment.paid_at),
            "createdAt": payment.created_at.isoformat(),
        }
        for payment in rows.scalars().all()
    ]


async def get_admin_payment_map(
    db: AsyncSession,
    limit: int = 50,
    cursor: str | None = None,
) -> dict:
    query = select(Payment).order_by(Payment.created_at.desc()).limit(limit)
    rows = await db.execute(query)
    items = rows.scalars().all()
    return {
        "payments": [_payment_to_response(p).model_dump(by_alias=True) for p in items],
        "cursor": items[-1].id if items else None,
    }


async def get_agency_payout_summary(db: AsyncSession, agency_id: str) -> dict:
    wallet = await get_agency_wallet_summary(db, agency_id)
    rows = await db.execute(
        select(Payment)
        .where(Payment.agency_id == agency_id)
        .order_by(Payment.created_at.desc())
        .limit(100)
    )
    payments = rows.scalars().all()
    return {
        "wallet": wallet.model_dump(by_alias=True),
        "payments": [_payment_to_response(p).model_dump(by_alias=True) for p in payments],
    }


async def execute_agency_payout(db: AsyncSession, payment_id: str, tranche: str) -> dict:
    payment = await db.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        raise NotFoundError("Payment")

    if payment.payout_frozen:
        raise BadRequestError(
            "This payment has an open Razorpay chargeback — payouts are frozen until it's resolved."
        )

    already_released = bool(payment.tranche2_released) if tranche == "tranche2" else bool(payment.tranche1_released)
    if already_released:
        # Idempotent: never re-attempt a Razorpay transfer for a tranche
        # that's already marked released.
        return {
            "paymentId": payment.id,
            "tranche": tranche,
            "escrowStatus": payment.escrow_status,
            "releasedAt": datetime.now(UTC).isoformat(),
        }

    ctx = await inv_svc._trip_context(db, payment)
    agency = ctx["agency"]
    agency_net = int((payment.trip_amount or 0) - (payment.commission_amount or 0))
    amount = _tranche_amount(agency_net, tranche)

    bank = (
        await db.scalar(select(AgencyBankAccount).where(AgencyBankAccount.agency_id == agency.id))
        if agency else None
    )
    is_real_payment = bool(payment.razorpay_payment_id) and not payment.razorpay_payment_id.startswith("pay_mock_")
    can_route_transfer = bool(
        settings.razorpay_key_id
        and settings.razorpay_key_secret
        and bank
        and bank.razorpay_account_id
        and is_real_payment
        and amount > 0
    )

    # Seam: without a Razorpay linked account on file (true for every agency
    # today — see agency_bank_accounts.razorpayAccountId), this falls back to
    # the pre-existing manual/bookkeeping-only payout. Once an agency is
    # onboarded through Razorpay's Linked Account/KYC flow (not built here),
    # this same call starts moving real money via Razorpay Route.
    transfer_id = None
    if can_route_transfer:
        try:
            transfer = create_transfer(
                payment.razorpay_payment_id,
                bank.razorpay_account_id,
                amount,
                notes={"paymentId": payment.id, "tranche": tranche},
            )
            transfer_id = transfer.get("id")
        except PaymentError as exc:
            logger.error("Razorpay Route transfer failed for payment %s (%s): %s", payment.id, tranche, exc)
            payment.transfer_status = "FAILED"
            await db.flush()
            raise BadRequestError(
                "Razorpay transfer failed — the agency has not been paid. Check the linked account and retry."
            ) from exc

    if tranche == "tranche2":
        payment.tranche2_released = True
    else:
        payment.tranche1_released = True

    if payment.tranche1_released and payment.tranche2_released:
        payment.escrow_status = "RELEASED"
        payment.transfer_status = "SETTLED"
    else:
        payment.escrow_status = "PARTIAL_RELEASE"
        payment.transfer_status = "SETTLED" if transfer_id else "PROCESSING"

    if agency:
        wallet = await db.scalar(select(AgencyWallet).where(AgencyWallet.agency_id == agency.id))
        if wallet:
            wallet.available_balance = int(wallet.available_balance or 0) + amount
            wallet.total_earned = int(wallet.total_earned or 0) + amount
        db.add(
            AgencyTransaction(
                id=str(uuid.uuid4()),
                wallet_id=wallet.id if wallet else "",
                type="PAYOUT_ROUTE" if transfer_id else "PAYOUT_MANUAL",
                amount=amount,
                description=f"{tranche} payout for payment {payment.id}",
                payment_id=payment.id,
                razorpay_transfer_id=transfer_id,
            )
        )

    await db.flush()
    await _send_payout_notification(db, payment, tranche)
    return {
        "paymentId": payment.id,
        "tranche": tranche,
        "escrowStatus": payment.escrow_status,
        "transferId": transfer_id,
        "releasedAt": datetime.now(UTC).isoformat(),
    }


async def resolve_confirming_window(db: AsyncSession, group_id: str) -> dict:
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")

    if group.plan_id:
        plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id))
        if plan and plan.status == "CONFIRMING":
            plan.status = "CONFIRMED"
            plan.confirmed_at = datetime.now(UTC)

    if group.package_id:
        package = await db.scalar(select(Package).where(Package.id == group.package_id))
        if package and package.status == "CONFIRMING":
            package.status = "CONFIRMED"

    await db.flush()
    return {"groupId": group_id, "status": "resolved"}


async def reconcile_pending_payments(db: AsyncSession, limit: int = 50) -> dict:
    rows = await db.execute(
        select(Payment)
        .where(Payment.status == "PENDING")
        .order_by(Payment.created_at.asc())
        .limit(limit)
    )
    items = rows.scalars().all()
    return {"checked": len(items), "updated": 0}


async def complete_trip(db: AsyncSession, group_id: str) -> dict:
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")

    if group.plan_id:
        plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id))
        if plan:
            plan.status = "COMPLETED"

    if group.package_id:
        package = await db.scalar(select(Package).where(Package.id == group.package_id))
        if package:
            package.status = "COMPLETED"

    # Route every outstanding tranche through the same payout path the manual
    # admin endpoint uses (execute_agency_payout) instead of flipping
    # escrow/transfer flags directly. The old code marked tranche2 SETTLED
    # and emailed the agency a "final settlement" notice without ever calling
    # create_transfer or crediting AgencyWallet — harmless only because no
    # agency has a linked Razorpay account yet, but it would tell agencies
    # they'd been paid when no money had moved. Only CAPTURED payments are
    # eligible; a PENDING/FAILED payment has no money to release.
    rows = await db.execute(
        select(Payment).where(Payment.group_id == group_id, Payment.status == "CAPTURED")
    )
    payments = rows.scalars().all()
    payout_failures: list[str] = []
    for payment in payments:
        for tranche, released in (("tranche1", payment.tranche1_released), ("tranche2", payment.tranche2_released)):
            if released:
                continue
            try:
                await execute_agency_payout(db, payment.id, tranche)
            except Exception:
                # A payout failure (e.g. a real Route transfer rejected)
                # must not block marking the trip itself as completed —
                # that's a logistics fact independent of settlement status.
                # execute_agency_payout already persists transfer_status
                # FAILED before raising, so it's visible for admin retry via
                # the existing idempotent /payments/agency/payout endpoint.
                logger.exception("Trip completion payout failed for payment %s (%s)", payment.id, tranche)
                payout_failures.append(f"{payment.id}:{tranche}")

    await db.flush()
    return {"groupId": group_id, "status": "COMPLETED", "payoutFailures": payout_failures}
