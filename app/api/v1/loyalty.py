from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.exceptions import ForbiddenError
from app.models.enums import UserRole
from app.services import loyalty as loyalty_svc

router = APIRouter(prefix="/loyalty", tags=["loyalty"])


def _block_agencies(current_user: CurrentUser) -> None:
    """Refer & Earn is a consumer/traveler feature (PRD 2.3) — agency
    accounts get a clear 403, not a silently empty/hidden UI element."""
    if current_user.role == UserRole.AGENCY_ADMIN:
        raise ForbiddenError("Refer & Earn is not available for agency accounts")


@router.get("/balance")
async def get_balance(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await loyalty_svc.get_loyalty_balance(db, current_user.user_id)


@router.get("/ledger")
async def get_ledger(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, total = await loyalty_svc.get_loyalty_ledger(db, current_user.user_id, page, page_size)
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "pages": int((total + page_size - 1) // page_size) if page_size > 0 else 0,
        },
    }


@router.post("/admin/adjust")
async def admin_adjust_points(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    current_user.require_admin()
    return {"ok": True, "message": "Adjustment endpoint is available", "payload": payload}


@router.post("/admin/expire")
async def admin_expire_points(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    current_user.require_admin()
    return {"ok": True, "message": "Expiry endpoint is available", "payload": payload}


router_referrals = APIRouter(prefix="/referrals", tags=["referrals"])


@router_referrals.get("/my-link")
async def get_my_referral_link(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_or_create_referral_link(db, current_user.user_id)


@router_referrals.post("/generate-link")
async def generate_referral_link(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.generate_referral_link(db, current_user.user_id)


@router_referrals.get("/my")
async def get_my_agency_referrals(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await loyalty_svc.get_agency_referrals(db, current_user.user_id)


@router_referrals.get("/my-referrals")
async def get_my_referrals(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_my_referrals(db, current_user.user_id, page, page_size)


@router_referrals.get("/stats")
async def get_referral_stats(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_referral_stats(db, current_user.user_id)


@router_referrals.get("/metrics")
async def get_referral_metrics(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_referral_metrics(db, current_user.user_id)


router_wallet = APIRouter(prefix="/wallet", tags=["wallet"])


@router_wallet.get("/balance")
async def get_wallet_balance(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_wallet_balance(db, current_user.user_id)


@router_wallet.get("/transactions")
async def get_wallet_transactions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200, alias="pageSize"),
    type: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_wallet_transactions(
        db,
        current_user.user_id,
        page,
        page_size,
        type,
    )


@router_wallet.get("/monthly-summary")
async def get_monthly_summary(
    months_back: int = Query(default=12, ge=1, le=36, alias="monthsBack"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _block_agencies(current_user)
    return await loyalty_svc.get_wallet_monthly_summary(db, current_user.user_id, months_back)


@router_wallet.get("/admin/cashflow-audit")
async def get_cashflow_audit(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()

    start = datetime.fromisoformat(start_date) if start_date else datetime(2026, 1, 1)
    end = datetime.fromisoformat(end_date) if end_date else datetime.utcnow()
    return await loyalty_svc.get_cashflow_audit(db, start, end)


@router_wallet.get("/admin/reconciliation")
async def get_reconciliation(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()

    start = datetime.fromisoformat(start_date) if start_date else datetime(2026, 1, 1)
    end = datetime.fromisoformat(end_date) if end_date else datetime.utcnow()
    return await loyalty_svc.get_reconciliation_report(db, start, end)
