from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import UserMeta
from app.schemas.social import PublicProfileResponse
from app.services.social import get_public_profile

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/search")
async def search_users(
    q: str = Query(..., min_length=2),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User
    from sqlalchemy import func

    search_term = f"%{q}%"
    total = await db.scalar(
        select(func.count(User.id)).where(
            User.is_active == True,
            (User.username.ilike(search_term) | User.display_name.ilike(search_term)),
        )
    ) or 0

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
    items = [
        UserMeta(id=u.id, username=u.username, display_name=u.display_name, avatar_url=u.avatar_url)
        for u in users
    ]
    from app.core.pagination import make_pagination
    return make_pagination(page, page_size, total, items)


@router.get("/profile/{username}", response_model=PublicProfileResponse)
async def get_profile(username: str, db: AsyncSession = Depends(get_db)):
    return await get_public_profile(db, username)
