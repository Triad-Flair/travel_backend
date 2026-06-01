from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.services import notifications as notif_svc

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    limit: int = Query(default=40, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await notif_svc.list_notifications(db, current_user.user_id, limit)


@router.post("/{notif_id}/read")
async def mark_read(
    notif_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await notif_svc.mark_notification_read(db, notif_id, current_user.user_id)


@router.post("/read-all")
async def mark_all_read(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await notif_svc.mark_all_read(db, current_user.user_id)


@router.get("/profile-views")
async def get_profile_views(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await notif_svc.get_profile_views(db, current_user.user_id, limit)
