import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.lib.pdf import render_agency_settlement_pdf, render_user_invoice_pdf
from app.models.agency import Agency
from app.models.group import Group, GroupMember
from app.models.offer import Offer
from app.models.package import Package
from app.models.payment import Invoice, Payment
from app.models.plan import Plan
from app.models.user import User
from app.exceptions import ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)
from app.schemas.invoices import (
    AgencySettlementResponse,
    InvoiceAgencyInfo,
    InvoiceClientInfo,
    InvoiceDiscountLine,
    InvoiceLineItem,
    InvoiceSummary,
    InvoiceTravelerInfo,
    InvoiceTripInfo,
    PlatformInfo,
    SettlementInfo,
    SettlementPaymentInfo,
    SettlementTripInfo,
    UserInvoiceResponse,
    UserPaymentInfo,
)

PLATFORM_INFO = {
    "name": "Triad Flair IT Solutions LLP",
    "brandName": "Trawell Buddy",
    "tagline": "India's Social Travel Platform",
    "address": "H65, Sector 63, Noida, Uttar Pradesh 201301",
    "gstin": "09ABAFT1926H1Z6",
    "email": "connect@triadflair.com",
    "website": "https://trawellbuddy.com",
    "llpin": "ACY-7379",
    "supportEmail": "connect@triadflair.com",
    "supportPhone": "+91 92056 29899",
}

REFUND_POLICY = [
    {"window": "30+ days before trip", "refund": "100% refund (less payment gateway charges)"},
    {"window": "15–29 days before trip", "refund": "75% refund"},
    {"window": "7–14 days before trip", "refund": "50% refund"},
    {"window": "2–6 days before trip", "refund": "25% refund"},
    {"window": "Less than 48 hours", "refund": "No refund"},
]

TERMS = [
    "Funds are held in escrow until trip milestones are completed.",
    f"{PLATFORM_INFO['name']} acts as an intermediary platform between traveler and agency.",
    "Cancellation refunds depend on selected package/offer policy.",
    "This invoice is system generated and valid without signature.",
]


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _invoice_number(payment_id: str, prefix: str = "TSU") -> str:
    short = payment_id.replace("-", "")[:8].upper()
    year = datetime.utcnow().year
    return f"{prefix}-{year}-{short}"


async def _resolve_payment(db: AsyncSession, identifier: str) -> Payment:
    payment = await db.scalar(select(Payment).where(Payment.id == identifier))
    if payment:
        return payment

    invoice = await db.scalar(select(Invoice).where(Invoice.id == identifier))
    if not invoice:
        raise NotFoundError("Payment")

    payment = await db.scalar(select(Payment).where(Payment.id == invoice.payment_id))
    if not payment:
        raise NotFoundError("Payment")
    return payment


async def _ensure_invoice(db: AsyncSession, payment: Payment) -> Invoice:
    agency_id = payment.agency_id
    if not agency_id:
        group = await db.scalar(select(Group).where(Group.id == payment.group_id))
        if group and group.plan_id:
            plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id))
            if plan and plan.confirmed_offer_id:
                offer = await db.scalar(select(Offer).where(Offer.id == plan.confirmed_offer_id))
                if offer:
                    agency_id = offer.agency_id
        if group and group.package_id and not agency_id:
            package = await db.scalar(select(Package).where(Package.id == group.package_id))
            if package:
                agency_id = package.agency_id

    invoice = await db.scalar(select(Invoice).where(Invoice.payment_id == payment.id))
    if invoice:
        invoice.group_id = payment.group_id
        invoice.agency_id = agency_id or invoice.agency_id
        invoice.user_id = payment.user_id
        invoice.amount = int(payment.trip_amount or payment.amount)
        invoice.platform_fee_amount = int(payment.platform_fee_amount or 0)
        invoice.fee_gst_amount = int(payment.fee_gst_amount or 0)
        invoice.commission_amount = int(payment.commission_amount or 0)
        invoice.total_amount = int(payment.amount)
        invoice.currency = payment.currency
        invoice.status = payment.status
        await db.flush()
        return invoice

    invoice = Invoice(
        id=str(uuid.uuid4()),
        group_id=payment.group_id,
        payment_id=payment.id,
        agency_id=agency_id or "",
        user_id=payment.user_id,
        invoice_number=_invoice_number(payment.id, "TSU"),
        amount=int(payment.trip_amount or payment.amount),
        platform_fee_amount=int(payment.platform_fee_amount or 0),
        fee_gst_amount=int(payment.fee_gst_amount or 0),
        commission_amount=int(payment.commission_amount or 0),
        total_amount=int(payment.amount),
        currency=payment.currency,
        status=payment.status,
        pdf_url=None,
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def _trip_context(db: AsyncSession, payment: Payment) -> dict:
    group = await db.scalar(select(Group).where(Group.id == payment.group_id))
    if not group:
        raise NotFoundError("Group")

    plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id)) if group.plan_id else None
    package = await db.scalar(select(Package).where(Package.id == group.package_id)) if group.package_id else None

    offer = None
    if plan and plan.confirmed_offer_id:
        offer = await db.scalar(select(Offer).where(Offer.id == plan.confirmed_offer_id))

    agency = await db.scalar(select(Agency).where(Agency.id == payment.agency_id)) if payment.agency_id else None
    if not agency and offer:
        agency = await db.scalar(select(Agency).where(Agency.id == offer.agency_id))
    if not agency and package:
        agency = await db.scalar(select(Agency).where(Agency.id == package.agency_id))

    member_rows = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.status.in_(["APPROVED", "COMMITTED"]),
        )
    )
    members = member_rows.scalars().all()

    member_names = []
    for m in members:
        user = await db.scalar(select(User).where(User.id == m.user_id))
        if user:
            member_names.append(user.display_name or user.username or "Traveler")

    return {
        "group": group,
        "plan": plan,
        "package": package,
        "offer": offer,
        "agency": agency,
        "members": members,
        "member_names": member_names,
    }


async def list_user_invoices(db: AsyncSession, user_id: str) -> list[dict]:
    """Only CAPTURED payments — a PENDING/AUTHORIZED/FAILED payment was
    never actually charged, so surfacing an "invoice" for it here just
    confused travelers with unpaid trips sitting in their invoice list."""
    rows = await db.execute(
        select(Payment)
        .where(Payment.user_id == user_id, Payment.status == "CAPTURED")
        .order_by(Payment.created_at.desc())
    )
    payments = rows.scalars().all()

    items = []
    for payment in payments:
        invoice = await _ensure_invoice(db, payment)
        ctx = await _trip_context(db, payment)
        plan = ctx["plan"]
        package = ctx["package"]

        items.append(
            {
                "id": payment.id,
                "invoiceNumber": invoice.invoice_number,
                "totalAmount": int(invoice.total_amount),
                "currency": invoice.currency,
                "status": invoice.status,
                "createdAt": _iso(invoice.created_at),
                "payment": {
                    "status": payment.status,
                    "paidAt": _iso(payment.paid_at),
                    "planId": None,
                    "packageId": None,
                },
                "group": {
                    "plan": {
                        "title": plan.title,
                        "destination": plan.destination,
                        "startDate": _iso(plan.start_date),
                    } if plan else None,
                    "package": {
                        "title": package.title,
                        "destination": package.destination,
                        "startDate": _iso(package.start_date),
                    } if package else None,
                },
            }
        )

    return items


async def list_agency_invoices(db: AsyncSession, agency_id: str) -> list[dict]:
    rows = await db.execute(
        select(Payment)
        .where(Payment.agency_id == agency_id)
        .order_by(Payment.created_at.desc())
    )
    payments = rows.scalars().all()

    items = []
    for payment in payments:
        invoice = await _ensure_invoice(db, payment)
        ctx = await _trip_context(db, payment)
        plan = ctx["plan"]
        package = ctx["package"]
        agency_net = int((payment.trip_amount or 0) - (payment.commission_amount or 0))

        items.append(
            {
                "id": payment.id,
                "invoiceNumber": invoice.invoice_number.replace("TSU", "TSA"),
                "totalAmount": int(invoice.total_amount),
                "currency": invoice.currency,
                "status": invoice.status,
                "createdAt": _iso(invoice.created_at),
                "payment": {
                    "status": payment.status,
                    "paidAt": _iso(payment.paid_at),
                    "agencyNetAmount": agency_net,
                    "payoutReleased": bool(payment.payout_released),
                },
                "group": {
                    "plan": {
                        "title": plan.title,
                        "destination": plan.destination,
                        "startDate": _iso(plan.start_date),
                    } if plan else None,
                    "package": {
                        "title": package.title,
                        "destination": package.destination,
                        "startDate": _iso(package.start_date),
                    } if package else None,
                },
            }
        )

    return items


async def build_user_invoice_payload(db: AsyncSession, payment_id: str, requesting_user_id: str) -> UserInvoiceResponse:
    payment = await _resolve_payment(db, payment_id)
    if payment.user_id != requesting_user_id:
        raise ForbiddenError("Access denied")

    invoice = await _ensure_invoice(db, payment)
    return await _build_user_invoice_data(db, payment, invoice)


async def _build_user_invoice_data(db: AsyncSession, payment: Payment, invoice: Invoice) -> UserInvoiceResponse:
    """Split from build_user_invoice_payload so the payment-capture flow
    (which has no "requesting user" — it's a system-triggered PDF render,
    not a GET request) can build the same payload without the access check."""
    traveler = await db.scalar(select(User).where(User.id == payment.user_id))
    ctx = await _trip_context(db, payment)

    plan = ctx["plan"]
    package = ctx["package"]
    offer = ctx["offer"]
    agency = ctx["agency"]
    member_names = ctx["member_names"]
    traveler_count = max(1, len(member_names))

    trip_title = plan.title if plan else package.title
    destination = plan.destination if plan else package.destination
    start_date = _iso(plan.start_date) if plan else _iso(package.start_date)
    end_date = _iso(plan.end_date) if plan else _iso(package.end_date)

    trip_amount = int(payment.trip_amount or (payment.amount - int(payment.platform_fee_amount or 0) - int(payment.fee_gst_amount or 0)))
    platform_fee_amount = int(payment.platform_fee_amount or 0)
    fee_gst_amount = int(payment.fee_gst_amount or 0)
    points_redeemed = int(payment.points_redeemed or 0)
    wallet_amount_used = int(payment.wallet_amount_used or 0)

    points_discount = points_redeemed * 100
    wallet_discount = wallet_amount_used * 100
    promo_discount = int(payment.promo_discount_amount or 0)
    total_discounts = points_discount + wallet_discount + promo_discount
    grand_total = int(payment.amount)

    per_person_rate = int(trip_amount / traveler_count) if traveler_count > 0 else trip_amount
    discount_lines = []
    if payment.promo_code and promo_discount > 0:
        discount_lines.append(
            InvoiceDiscountLine(label=f"Coupon Applied ({payment.promo_code})", amount=-promo_discount)
        )
    if points_redeemed > 0:
        discount_lines.append(
            InvoiceDiscountLine(label=f"Loyalty Points Redeemed ({points_redeemed} pts × ₹1)", amount=-points_discount)
        )
    if wallet_amount_used > 0:
        discount_lines.append(InvoiceDiscountLine(label="Wallet Credit Applied", amount=-wallet_discount))

    return UserInvoiceResponse(
        invoice_number=invoice.invoice_number,
        issued_at=_iso(payment.paid_at or invoice.created_at),
        status=payment.status,
        escrow_status=payment.escrow_status,
        platform=PlatformInfo(**PLATFORM_INFO),
        traveler=InvoiceTravelerInfo(
            name=(traveler.display_name or traveler.username or "Traveler") if traveler else "Traveler",
            email=traveler.email if traveler else "",
            phone=traveler.phone if traveler else None,
            city=traveler.location if traveler else "",
        ),
        agency=InvoiceAgencyInfo(
            name=agency.name,
            gstin=agency.gstin or "Pending verification",
            pan=agency.pan or "",
            address=", ".join([p for p in [agency.address, agency.city, agency.state] if p]),
            email=agency.email or "",
            phone=agency.phone or "",
            logo_url=agency.logo_url,
        ) if agency else None,
        trip=InvoiceTripInfo(
            title=trip_title,
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            traveler_count=traveler_count,
            price_per_person=int(offer.price_per_person * 100) if offer else per_person_rate,
            inclusions=[],
            cancellation_policy=(offer.cancellation_policy if offer else (package.cancellation_policy if package else "Standard policy")) or "Standard policy",
            plan_type=plan.plan_type if plan else "STANDARD",
            accommodation=(plan.accommodation if plan else package.accommodation if package else None),
            vibes=[],
        ),
        line_items=[
            item for item in [
                InvoiceLineItem(
                    description=f"{trip_title} — {destination}",
                    subtext=f"{start_date or 'TBD'} → {end_date or 'TBD'}",
                    qty=traveler_count,
                    unit="person",
                    rate=per_person_rate,
                    subtotal=trip_amount,
                ),
                InvoiceLineItem(
                    description="GST (5%)",
                    subtext="Goods & Services Tax on package price",
                    qty=1,
                    unit="fixed",
                    rate=fee_gst_amount,
                    subtotal=fee_gst_amount,
                ) if fee_gst_amount > 0 else None,
            ] if item is not None
        ],
        summary=InvoiceSummary(
            subtotal=trip_amount + fee_gst_amount,
            trip_amount=trip_amount,
            platform_fee=platform_fee_amount,
            gst_on_platform_fee=fee_gst_amount,
            discount_lines=discount_lines,
            total_discounts=-total_discounts,
            grand_total=grand_total,
        ),
        payment=UserPaymentInfo(
            id=payment.id,
            razorpay_order_id=payment.razorpay_order_id,
            razorpay_payment_id=payment.razorpay_payment_id,
            currency=payment.currency,
            paid_at=_iso(payment.paid_at),
        ),
        points_redeemed=points_redeemed,
        wallet_amount_used=wallet_amount_used,
        refund_policy=REFUND_POLICY,
        terms_and_conditions=TERMS,
        members=member_names,
    )


async def build_agency_settlement_payload(db: AsyncSession, payment_id: str, requesting_user_id: str) -> AgencySettlementResponse:
    payment = await _resolve_payment(db, payment_id)
    invoice = await _ensure_invoice(db, payment)
    ctx = await _trip_context(db, payment)

    agency = ctx["agency"]
    if not agency:
        raise NotFoundError("Agency")
    if agency.owner_id != requesting_user_id:
        raise ForbiddenError("Access denied")

    return await _build_agency_settlement_data(db, payment, invoice, ctx)


async def _build_agency_settlement_data(db: AsyncSession, payment: Payment, invoice: Invoice, ctx: dict | None = None) -> AgencySettlementResponse:
    """Split from build_agency_settlement_payload — see _build_user_invoice_data
    for why (system-triggered PDF render, no requesting user to check)."""
    if ctx is None:
        ctx = await _trip_context(db, payment)
    agency = ctx["agency"]
    if not agency:
        raise NotFoundError("Agency")

    traveler = await db.scalar(select(User).where(User.id == payment.user_id))
    plan = ctx["plan"]
    package = ctx["package"]
    member_names = ctx["member_names"]

    trip_title = plan.title if plan else package.title
    destination = plan.destination if plan else package.destination
    start_date = _iso(plan.start_date) if plan else _iso(package.start_date)
    end_date = _iso(plan.end_date) if plan else _iso(package.end_date)

    trip_amount = int(payment.trip_amount or 0)
    platform_fee = int(payment.platform_fee_amount or 0)
    gst_fee = int(payment.fee_gst_amount or 0)
    commission = int(payment.commission_amount or 0)
    agency_net = trip_amount - commission
    owner_user = await db.scalar(select(User).where(User.id == agency.owner_id)) if agency.owner_id else None

    return AgencySettlementResponse(
        invoice_number=invoice.invoice_number.replace("TSU", "TSA"),
        issued_at=_iso(payment.paid_at or invoice.created_at),
        status=payment.escrow_status,
        transfer_status=payment.transfer_status or "MANUAL",
        platform=PlatformInfo(**PLATFORM_INFO),
        agency=InvoiceAgencyInfo(
            name=agency.name,
            gstin=agency.gstin or "Pending",
            pan=agency.pan or "",
            address=", ".join([p for p in [agency.address, agency.city, agency.state] if p]),
            email=agency.email or "",
            phone=agency.phone or "",
            owner_name=(owner_user.display_name or owner_user.username or "") if owner_user else "",
            logo_url=agency.logo_url,
        ),
        client=InvoiceClientInfo(
            name=(traveler.display_name or traveler.username or "Traveler") if traveler else "Traveler",
            email=traveler.email if traveler else "",
            phone=traveler.phone if traveler else None,
        ),
        trip=SettlementTripInfo(
            title=trip_title,
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            traveler_count=max(1, len(member_names)),
        ),
        settlement=SettlementInfo(
            trip_amount=trip_amount,
            platform_commission=commission,
            platform_fee=platform_fee,
            gst_on_fee=gst_fee,
            agency_net_amount=agency_net,
            payout_released=bool(payment.payout_released),
        ),
        payment=SettlementPaymentInfo(
            id=payment.id,
            razorpay_order_id=payment.razorpay_order_id,
            razorpay_payment_id=payment.razorpay_payment_id,
            currency=payment.currency,
            paid_at=_iso(payment.paid_at),
            transfer_reference=None,
        ),
        members=member_names,
        terms_and_conditions=TERMS,
    )


async def ensure_invoice_pdfs(db: AsyncSession, payment: Payment) -> Invoice:
    """Called once at payment capture — generates and stores both PDFs
    (idempotent: skips whichever blob is already populated, so calling this
    again after a partial failure only fills the gap, and repeat capture-
    notification retries don't re-render for nothing). A PDF failure must
    never block the payment capture flow itself, so every render is
    isolated in its own try/except and just leaves that blob unset on error
    — list_user_invoices/list_agency_invoices calling this again later
    (e.g. the traveler opening their invoice tab) gets another chance."""
    invoice = await _ensure_invoice(db, payment)
    generated_any = False

    if invoice.user_pdf_data is None:
        try:
            user_payload = await _build_user_invoice_data(db, payment, invoice)
            invoice.user_pdf_data = render_user_invoice_pdf(user_payload)
            generated_any = True
        except Exception:
            logger.exception("User invoice PDF generation failed for payment %s", payment.id)

    if invoice.agency_pdf_data is None:
        try:
            ctx = await _trip_context(db, payment)
            if ctx["agency"]:
                agency_payload = await _build_agency_settlement_data(db, payment, invoice, ctx)
                invoice.agency_pdf_data = render_agency_settlement_pdf(agency_payload)
                generated_any = True
        except Exception:
            logger.exception("Agency settlement PDF generation failed for payment %s", payment.id)

    if generated_any:
        invoice.pdf_generated_at = datetime.now(UTC)
        await db.flush()

    return invoice


async def get_user_invoice_pdf(db: AsyncSession, payment_id: str, requesting_user_id: str) -> tuple[bytes, str]:
    payment = await _resolve_payment(db, payment_id)
    if payment.user_id != requesting_user_id:
        raise ForbiddenError("Access denied")
    invoice = await ensure_invoice_pdfs(db, payment)
    if not invoice.user_pdf_data:
        raise NotFoundError("Invoice PDF (generation failed — try again shortly)")
    return invoice.user_pdf_data, invoice.invoice_number


async def get_agency_settlement_pdf(db: AsyncSession, payment_id: str, requesting_user_id: str) -> tuple[bytes, str]:
    payment = await _resolve_payment(db, payment_id)
    ctx = await _trip_context(db, payment)
    agency = ctx["agency"]
    if not agency:
        raise NotFoundError("Agency")
    if agency.owner_id != requesting_user_id:
        raise ForbiddenError("Access denied")
    invoice = await ensure_invoice_pdfs(db, payment)
    if not invoice.agency_pdf_data:
        raise NotFoundError("Settlement PDF (generation failed — try again shortly)")
    return invoice.agency_pdf_data, invoice.invoice_number.replace("TSU", "TSA")
