from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.schemas.invoices import AgencySettlementResponse, UserInvoiceResponse
from app.services import invoices as inv_svc

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("/me")
async def list_user_invoices(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await inv_svc.list_user_invoices(db, current_user.user_id)


@router.get("/agency/me")
async def list_agency_invoices(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await inv_svc.list_agency_invoices(db, agency_id)


@router.get("/agency/settlement/{payment_id}", response_model=AgencySettlementResponse)
async def agency_settlement_detail(
    payment_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await inv_svc.build_agency_settlement_payload(db, payment_id, current_user.user_id)


@router.get("/{payment_id}", response_model=UserInvoiceResponse)
async def user_invoice_detail(
    payment_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await inv_svc.build_user_invoice_payload(db, payment_id, current_user.user_id)
