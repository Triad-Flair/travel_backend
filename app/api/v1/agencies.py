from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user, get_optional_user
from app.schemas.agencies import (
    CreateAgencyRequest,
    GstVerifyResponse,
    IfscLookupResponse,
    InviteMemberRequest,
    UpdateAgencyRequest,
)
from app.services import agencies as ag_svc

router = APIRouter(prefix="/agencies", tags=["agencies"])


@router.get("/browse")
async def browse(
    city: str | None = Query(default=None),
    state: str | None = Query(default=None),
    specialization: str | None = Query(default=None),
    destination: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    limit: int | None = Query(default=None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    effective_page_size = limit or page_size
    items, _total = await ag_svc.browse_agencies(
        db,
        city,
        state,
        specialization,
        destination,
        page,
        effective_page_size,
    )
    return items


@router.get("/gst/verify", response_model=GstVerifyResponse)
async def verify_gst(gstin: str = Query(...), db: AsyncSession = Depends(get_db)):
    return await ag_svc.verify_gst(gstin)


@router.get("/ifsc/lookup", response_model=IfscLookupResponse)
async def lookup_ifsc(code: str = Query(...)):
    return await ag_svc.lookup_ifsc(code)


@router.post("", status_code=201)
async def create_agency(
    req: CreateAgencyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ag_svc.create_agency(db, current_user.user_id, req)


@router.get("/{slug_or_id}")
async def get_agency(
    slug_or_id: str,
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    requesting_user_id = current_user.user_id if current_user else None
    return await ag_svc.get_agency_by_slug(db, slug_or_id, requesting_user_id)


@router.patch("/{agency_id}")
async def update_agency(
    agency_id: str,
    req: UpdateAgencyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ag_svc.update_agency(db, agency_id, current_user.user_id, req)


@router.post("/{agency_id}/verification/submit")
async def submit_verification(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ag_svc.submit_verification(db, agency_id, current_user.user_id, payload)


@router.get("/admin/pending-verification")
async def list_pending_verification(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    return await ag_svc.list_pending_verification_agencies(db)


@router.post("/{agency_id}/verification/approve")
async def approve_verification(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    return await ag_svc.approve_verification(db, agency_id)


@router.post("/{agency_id}/verification/reject")
async def reject_verification(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.require_admin()
    return await ag_svc.reject_verification(db, agency_id, payload.get("reason"))


@router.get("/{agency_id}/bank")
async def get_bank_record(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ag_svc.get_bank_record(db, agency_id, current_user.user_id)


@router.post("/{agency_id}/bank/verify")
async def verify_bank_record(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ag_svc.verify_bank_account(db, agency_id, current_user.user_id, payload)


@router.get("/{agency_id}/members")
async def list_members(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ag_svc.get_agency_members(db, agency_id)


@router.post("/{agency_id}/members", status_code=201)
async def invite_member(
    agency_id: str,
    req: InviteMemberRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"ok": True}


@router.patch("/{agency_id}/members/{user_id}")
async def update_member_role(
    agency_id: str,
    user_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": agency_id, "userId": user_id, "role": payload.get("role")}


@router.delete("/{agency_id}/members/{user_id}")
async def remove_member(
    agency_id: str,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": agency_id, "userId": user_id}


@router.get("/{agency_id}/analytics")
async def analytics(agency_id: str, db: AsyncSession = Depends(get_db)):
    return {
        "totalTrips": 0, "totalRevenuePaise": 0,
        "avgRating": 0.0, "reviewCount": 0,
        "activeOffers": 0, "monthlyBookings": [],
    }


@router.get("/{agency_id}/calendar")
async def calendar(agency_id: str, db: AsyncSession = Depends(get_db)):
    return []


@router.get("/{agency_id}/kyc/documents")
async def kyc_docs(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return []


@router.post("/{agency_id}/kyc/documents")
async def upload_kyc_document(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": agency_id, "documentType": payload.get("documentType")}


@router.get("/{agency_id}/kyc/documents/{doc_id}/download")
async def download_kyc_document(
    agency_id: str,
    doc_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"url": "", "agencyId": agency_id, "docId": doc_id}


@router.post("/{agency_id}/kyc/upload-url")
async def kyc_upload_url(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return {"url": "", "key": ""}


@router.get("/{agency_id}/insurance/quotes")
async def list_insurance_quotes(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return []


@router.post("/{agency_id}/insurance/quotes")
async def create_insurance_quote(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": agency_id, **payload}


@router.get("/{agency_id}/customers")
async def list_customers(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return []


@router.post("/{agency_id}/customers")
async def create_customer(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": agency_id, **payload}


@router.get("/{agency_id}/campaigns")
async def list_campaigns(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return []


@router.post("/{agency_id}/campaigns")
async def create_campaign(
    agency_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": agency_id, **payload}


@router.get("/{agency_id}/risk-flags")
async def list_risk_flags(
    agency_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    return []


@router.post("/trust/evaluate")
async def evaluate_trust(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return {"ok": True, "agencyId": payload.get("agencyId"), "trustScore": 0}
