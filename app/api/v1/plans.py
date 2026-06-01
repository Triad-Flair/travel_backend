from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.schemas.plans import (
    ConfirmPlanRequest,
    CreatePlanRequest,
    PlanDetails,
    ReferPlanRequest,
    UpdatePlanRequest,
)
from app.services import offers as offer_svc
from app.services import plans as plan_svc

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/slug/{slug}", response_model=PlanDetails)
async def get_by_slug(slug: str, db: AsyncSession = Depends(get_db)):
    return await plan_svc.get_plan_by_slug(db, slug)


@router.get("/my")
async def list_my_plans(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, _total = await plan_svc.list_my_plans(db, current_user.user_id, page, page_size)
    return items


@router.get("/corporate/open")
async def list_corporate_open(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    items, _total = await plan_svc.list_open_corporate_plans(db, page, page_size)
    return items


@router.get("/{plan_id}", response_model=PlanDetails)
async def get_by_id(plan_id: str, db: AsyncSession = Depends(get_db)):
    return await plan_svc.get_plan_by_id(db, plan_id)


@router.post("", response_model=PlanDetails, status_code=201)
async def create(
    req: CreatePlanRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await plan_svc.create_plan(db, current_user.user_id, req)


@router.patch("/{plan_id}", response_model=PlanDetails)
async def update(
    plan_id: str,
    req: UpdatePlanRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await plan_svc.update_plan(db, plan_id, current_user.user_id, req)


@router.post("/{plan_id}/publish", response_model=PlanDetails)
async def publish(
    plan_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await plan_svc.publish_plan(db, plan_id, current_user.user_id)


@router.post("/{plan_id}/cancel")
async def cancel(
    plan_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await plan_svc.cancel_plan(db, plan_id, current_user.user_id)


@router.post("/{plan_id}/refer")
async def refer(
    plan_id: str,
    req: ReferPlanRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await plan_svc.refer_plan_to_agencies(db, plan_id, current_user.user_id, req)


@router.post("/{plan_id}/confirm")
async def confirm_with_offer(
    plan_id: str,
    req: ConfirmPlanRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await plan_svc.get_plan_by_id(db, plan_id)


@router.get("/{plan_id}/offers")
async def list_offers(
    plan_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await offer_svc.list_plan_offers(db, plan_id, current_user.user_id)
