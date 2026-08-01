from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.schemas.offers import (
    CounterOfferRequest,
    OfferResponse,
    RejectOfferRequest,
    SubmitOfferRequest,
)
from app.services import offers as offer_svc

router = APIRouter(prefix="/offers", tags=["offers"])


@router.post("", response_model=OfferResponse, status_code=201)
async def submit_offer(
    req: SubmitOfferRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await offer_svc.submit_offer(db, agency_id, current_user.user_id, req)


@router.get("/my")
async def list_my_offers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    items, _total = await offer_svc.list_my_offers(db, agency_id, page, page_size)
    return items


@router.get("/by-counterpart/{counterpart_id}")
async def list_by_counterpart(
    counterpart_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """counterpart_id is the *other* DM participant's user id. Agency
    callers get their offers to that traveler; traveler callers get the
    offers from that agency (identified by its owner's user id) across
    all of their own plans."""
    if current_user.agency_id:
        return await offer_svc.list_offers_by_counterpart(db, current_user.agency_id, counterpart_id)
    return await offer_svc.list_offers_by_agency_owner_for_traveler(db, current_user.user_id, counterpart_id)


@router.get("/{offer_id}", response_model=OfferResponse)
async def get_offer(
    offer_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from app.models.offer import Offer
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.agency), selectinload(Offer.negotiations))
        .where(Offer.id == offer_id)
    )
    offer = result.scalar_one_or_none()
    if not offer:
        from app.exceptions import NotFoundError
        raise NotFoundError("Offer")
    return await offer_svc._offer_to_response(db, offer)


@router.post("/{offer_id}/counter", response_model=OfferResponse)
async def counter_offer(
    offer_id: str,
    req: CounterOfferRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await offer_svc.counter_offer(db, offer_id, current_user.user_id, current_user.role, req)


@router.post("/{offer_id}/accept", response_model=OfferResponse)
async def accept_offer(
    offer_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await offer_svc.accept_offer(db, offer_id, current_user.user_id)


@router.post("/{offer_id}/reject", response_model=OfferResponse)
async def reject_offer(
    offer_id: str,
    req: RejectOfferRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await offer_svc.reject_offer(db, offer_id, current_user.user_id, req)


@router.post("/{offer_id}/withdraw", response_model=OfferResponse)
async def withdraw_offer(
    offer_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await offer_svc.withdraw_offer(db, offer_id, agency_id)
