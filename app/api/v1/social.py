from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user, get_optional_user
from app.schemas.social import (
    FollowerEntry,
    FollowStateResponse,
    PublicProfileResponse,
    SocialFeedItem,
)
from app.services import social as social_svc

router = APIRouter(prefix="/social", tags=["social"])


@router.get("/feed", response_model=list[SocialFeedItem])
async def feed(
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.get_feed(db, limit)


@router.get("/feed/following", response_model=list[SocialFeedItem])
async def following_feed(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.get_following_feed(db, current_user.user_id, limit)


@router.get("/profiles/{handle}", response_model=PublicProfileResponse)
async def get_profile(handle: str, db: AsyncSession = Depends(get_db)):
    return await social_svc.get_public_profile(db, handle)


@router.get("/profiles/{handle}/follow-state", response_model=FollowStateResponse)
async def get_follow_state(
    handle: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.get_follow_state(db, handle, current_user.user_id)


@router.post("/profiles/{handle}/follow", response_model=FollowStateResponse)
async def follow(
    handle: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.follow_user(db, handle, current_user.user_id)


@router.delete("/profiles/{handle}/follow", response_model=FollowStateResponse)
async def unfollow(
    handle: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.unfollow_user(db, handle, current_user.user_id)


@router.post("/profiles/{handle}/view")
async def record_view(
    handle: str,
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = current_user.user_id if current_user else None
    return await social_svc.record_profile_view(db, handle, user_id)


@router.get("/profiles/{handle}/followers", response_model=list[FollowerEntry])
async def get_followers(
    handle: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.get_followers(db, handle, page, page_size)


@router.get("/profiles/{handle}/following", response_model=list[FollowerEntry])
async def get_following(
    handle: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await social_svc.get_following(db, handle, page, page_size)
