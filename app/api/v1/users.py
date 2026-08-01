from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import UserMeta
from app.schemas.social import PublicProfileResponse
from app.services.social import get_public_profile

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/search", response_model=list[UserMeta])
async def search_users(
    q: str = Query(..., min_length=2),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User

    search_term = f"%{q}%"
    result = await db.execute(
        select(User)
        .where(
            User.is_active == True,
            (User.username.ilike(search_term) | User.display_name.ilike(search_term)),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = result.scalars().all()
    return [
        UserMeta(
            id=u.id,
            full_name=u.display_name or u.username or "",
            username=u.username,
            avatar_url=u.avatar_url,
        )
        for u in users
    ]


@router.get("/profile/{username}", response_model=PublicProfileResponse)
async def get_profile(username: str, db: AsyncSession = Depends(get_db)):
    return await get_public_profile(db, username)
