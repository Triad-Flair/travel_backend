import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.lib.razorpay_client import verify_webhook_signature
from app.schemas.payments import (
    AgencyWalletSummary,
    AgencyWalletTransaction,
    CreateDisputeRequest,
    CreateOrderRequest,
    DisputeResponse,
    GroupPaymentOrderResponse,
    GroupPaymentStateResponse,
    MockCaptureRequest,
    PaymentRecordResponse,
    ValidatePromoRequest,
    ValidatePromoResponse,
    VerifyPaymentRequest,
)
from app.services import payments as pay_svc
from app.services import invoices as inv_svc

router = APIRouter(prefix="/payments", tags=["payments"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {"ok": True, "module": "payments"}


@router.get("/my", response_model=list[PaymentRecordResponse])
async def list_my_payments(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, _total = await pay_svc.list_my_payments(db, current_user.user_id, page, page_size)
    return items


@router.get("/groups/{group_id}", response_model=GroupPaymentStateResponse)
async def get_group_payment_state(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.get_group_payment_state(db, group_id, current_user.user_id)


@router.get("/groups/{group_id}/checkout")
async def get_checkout_breakdown(
    group_id: str,
    promo_code: str | None = Query(default=None, alias="promoCode"),
    points_to_redeem: int | None = Query(default=None, alias="pointsToRedeem"),
    wallet_amount_to_use: int | None = Query(default=None, alias="walletAmountToUse"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.get_checkout_breakdown(
        db,
        group_id,
        current_user.user_id,
        points_to_redeem=points_to_redeem,
        wallet_amount_to_use=wallet_amount_to_use,
    )


@router.post("/groups/{group_id}/order", response_model=GroupPaymentOrderResponse)
async def create_order(
    group_id: str,
    req: CreateOrderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.create_payment_order(db, group_id, current_user.user_id, req)


@router.post("/promo/validate", response_model=ValidatePromoResponse)
async def validate_promo(
    req: ValidatePromoRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.validate_promo(db, req, current_user.user_id)


@router.post("/verify", response_model=PaymentRecordResponse)
async def verify_payment(
    req: VerifyPaymentRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.verify_payment(db, req, current_user.user_id)


@router.post("/razorpay-callback")
async def razorpay_hosted_checkout_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Razorpay's hosted checkout page (full-page redirect, not the JS
    # overlay) POSTs the browser here as a plain form submit — no JWT, no
    # JSON body, and no request originator left to hand an error response
    # to. The signature is the only trust boundary; deliberately no auth
    # dependency here (same posture as /webhook/razorpay).
    form = await request.form()
    redirect_url = await pay_svc.handle_hosted_checkout_callback(
        db,
        form.get("razorpay_order_id"),
        form.get("razorpay_payment_id"),
        form.get("razorpay_signature"),
    )
    # 303: browser must GET the redirect target, not replay the POST.
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/mock-capture", response_model=PaymentRecordResponse)
async def mock_capture(
    req: MockCaptureRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.mock_capture(db, req, current_user.user_id)


@router.post("/disputes", response_model=DisputeResponse, status_code=201)
async def create_dispute(
    req: CreateDisputeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.create_dispute(db, req, current_user.user_id)


@router.get("/disputes")
async def list_disputes(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pay_svc.list_disputes_for_agency(db, agency_id)


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    return await pay_svc.resolve_dispute(
        db,
        dispute_id,
        resolution=str(payload.get("resolution") or "RESOLVED"),
        notes=payload.get("notes"),
    )


@router.get("/wallet/summary", response_model=AgencyWalletSummary)
async def get_agency_wallet_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pay_svc.get_agency_wallet_summary(db, agency_id)


@router.get("/wallet/transactions", response_model=list[AgencyWalletTransaction])
async def get_agency_wallet_transactions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pay_svc.list_agency_wallet_transactions(db, agency_id)


@router.get("/invoices")
async def list_invoices_for_user(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await inv_svc.list_user_invoices(db, current_user.user_id)


@router.post("/confirming-window/resolve")
async def resolve_confirming_window(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    group_id = str(payload.get("groupId") or "")
    return await pay_svc.resolve_confirming_window(db, group_id)


@router.post("/reconcile")
async def reconcile_payments(
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    return await pay_svc.reconcile_pending_payments(db, limit)


@router.post("/groups/{group_id}/complete")
async def complete_group_trip(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role.value not in {"agency_admin", "platform_admin"}:
        current_user.require_admin()
    return await pay_svc.complete_trip(db, group_id)


@router.get("/tracking")
async def payment_tracking(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await pay_svc.get_payment_tracking_map(db, current_user.user_id)


@router.get("/admin/map")
async def admin_payment_map(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    return await pay_svc.get_admin_payment_map(db, limit=limit, cursor=cursor)


@router.get("/agency/payouts")
async def agency_payouts(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pay_svc.get_agency_payout_summary(db, agency_id)


@router.post("/agency/payout/{payment_id}")
async def agency_payout(
    payment_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    tranche = "tranche2" if str(payload.get("tranche")) == "tranche2" else "tranche1"
    return await pay_svc.execute_agency_payout(db, payment_id, tranche)


# Razorpay's real payloads are a few KB; anything past this is either a
# misfire or an attempt to waste CPU/memory on JSON parsing before the
# signature check even runs. Checked before reading the body at all.
MAX_WEBHOOK_BODY_BYTES = 256 * 1024


@router.post("/webhook/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    content_length: int | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    if content_length is not None and content_length > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    # Fail closed, always — this endpoint mutates payment state with zero
    # other auth. A missing header used to skip verification entirely
    # (`if x_razorpay_signature and not verify(...)` is vacuously true when
    # the header is absent), letting anyone POST a forged "payment.captured"
    # body and mark any pending payment as paid. An unconfigured secret is
    # equally dangerous — silently allowing unsigned webhooks through is
    # exactly the misconfiguration this check exists to catch, not a case to
    # special-case around. RAZORPAY_WEBHOOK_SECRET must be set from the
    # Razorpay Dashboard (Settings → Webhooks) before this endpoint can
    # process anything; the primary capture path (/payments/verify, always
    # signature-checked) keeps working regardless.
    if not settings.razorpay_webhook_secret or not x_razorpay_signature or not verify_webhook_signature(
        body, x_razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event = data.get("event", "")
    # Different event categories nest their entity under different keys
    # (payment.*: payload.payment.entity; order.paid: also payload.order.entity;
    # payment.dispute.*: payload.dispute.entity + payload.payment.entity;
    # payment.downtime.*: payload.payment.downtime.entity; invoice.*:
    # payload.invoice.entity; payment_link.*: payload.payment_link.entity).
    # Hand the whole payload to the dispatcher rather than pre-extracting one
    # shape that's wrong for most of these.
    payload = data.get("payload", {})
    try:
        await pay_svc.handle_razorpay_webhook(db, event, payload)
    except Exception:
        logger.exception("Razorpay webhook processing failed for event %s", event)
        raise HTTPException(status_code=500, detail="Webhook processing failed")
    return {"ok": True}
