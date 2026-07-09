from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.schemas.groups import GroupSummaryResponse
from app.schemas.packages import CreatePackageRequest, PackageCardResponse, PackageDetails, UpdatePackageRequest
from app.services import packages as pkg_svc

router = APIRouter(prefix="/packages", tags=["packages"])


@router.get("/slug/{slug}", response_model=PackageDetails)
async def get_by_slug(slug: str, db: AsyncSession = Depends(get_db)):
    return await pkg_svc.get_package_by_slug(db, slug)


@router.get("/my", response_model=list[PackageCardResponse])
async def list_my_packages(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    items, _total = await pkg_svc.list_my_packages(db, agency_id, page, page_size)
    return items


@router.post("/{pkg_id}/view", status_code=204)
async def record_view(pkg_id: str):
    """Fire-and-forget view counter — increments Redis key, Celery flushes to DB nightly."""
    from app.workers.tasks import track_package_view
    track_package_view.delay(pkg_id)
    return Response(status_code=204)


@router.get("/{pkg_id}", response_model=PackageDetails)
async def get_by_id(pkg_id: str, db: AsyncSession = Depends(get_db)):
    return await pkg_svc.get_package_by_id(db, pkg_id)


@router.post("", response_model=PackageDetails, status_code=201)
async def create(
    req: CreatePackageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pkg_svc.create_package(db, agency_id, req)


@router.patch("/{pkg_id}", response_model=PackageDetails)
async def update(
    pkg_id: str,
    req: UpdatePackageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pkg_svc.update_package(db, pkg_id, agency_id, req)


@router.post("/{pkg_id}/publish", response_model=PackageDetails)
async def publish(
    pkg_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    return await pkg_svc.publish_package(db, pkg_id, agency_id)


@router.post("/{pkg_id}/book", response_model=GroupSummaryResponse, status_code=201)
async def book(
    pkg_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Instant checkout entry point (PRD 3): no intermediate confirm step —
    creates/joins the package's Group directly, ready for
    GET /payments/groups/{group_id}/checkout right after."""
    return await pkg_svc.book_package(db, pkg_id, current_user.user_id)
