from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user, get_optional_user
from app.schemas.discover import DiscoverFilters, DiscoverItem
from app.services import discover as disc_svc

router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("", response_model=list[DiscoverItem])
async def discover_feed(
    destination: str | None = Query(default=None),
    budget_min: int | None = Query(default=None, alias="budgetMin"),
    budget_max: int | None = Query(default=None, alias="budgetMax"),
    origin_type: str | None = Query(default=None, alias="originType"),
    plan_type: str | None = Query(default=None, alias="planType"),
    group_type: str | None = Query(default=None, alias="groupType"),
    vibes: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    audience: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    filters = DiscoverFilters(
        destination=destination,
        budget_min=budget_min,
        budget_max=budget_max,
        origin_type=origin_type,
        plan_type=plan_type,
        group_type=group_type,
        vibes=vibes,
        sort=sort,
        audience=audience,
        page=page,
        page_size=page_size,
    )
    requesting_agency_id = current_user.agency_id if current_user else None
    return await disc_svc.get_discover_feed(db, filters, requesting_agency_id)


@router.get("/trending", response_model=list[DiscoverItem])
async def trending(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    requesting_agency_id = current_user.agency_id if current_user else None
    return await disc_svc.get_trending(db, page, page_size, requesting_agency_id)


@router.get("/search", response_model=list[DiscoverItem])
async def search(
    q: str = Query(..., min_length=2),
    origin_type: str | None = Query(default=None, alias="originType"),
    vibes: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    requesting_agency_id = current_user.agency_id if current_user else None
    return await disc_svc.search(db, q, page, page_size, requesting_agency_id, origin_type, vibes)


@router.get("/following", response_model=list[DiscoverItem])
async def following_feed(
    q: str | None = Query(default=None),
    destination: str | None = Query(default=None),
    origin_type: str | None = Query(default=None, alias="originType"),
    vibes: str | None = Query(default=None),
    sort: str | None = Query(default="recent"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filters = DiscoverFilters(
        destination=destination,
        origin_type=origin_type,
        vibes=vibes,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return await disc_svc.get_following_feed(db, current_user.user_id, q or "", filters)
