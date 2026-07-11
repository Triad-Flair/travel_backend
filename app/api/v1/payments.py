import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
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


@router.post("/webhook/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()

    # A missing header must be rejected the same as a bad one — omitting
    # X-Razorpay-Signature previously skipped verification entirely, letting
    # anyone POST a forged "payment.captured" body and mark any pending
    # payment as paid. Only relax this when no webhook secret is configured
    # at all (local/dev before Razorpay is wired up).
    if settings.razorpay_webhook_secret:
        if not x_razorpay_signature or not verify_webhook_signature(body, x_razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event = data.get("event", "")
    payload = data.get("payload", {}).get("payment", {}).get("entity", {})
    try:
        await pay_svc.handle_razorpay_webhook(db, event, payload)
    except Exception:
        logger.exception("Razorpay webhook processing failed for event %s", event)
        raise HTTPException(status_code=500, detail="Webhook processing failed")
    return {"ok": True}
