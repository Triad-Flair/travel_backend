from fastapi import APIRouter, Depends, Query

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user, get_optional_user
from app.schemas.posts import CreateCommentRequest, CreatePostRequest, PostCommentResponse, PostResponse
from app.services import posts as post_svc

router = APIRouter(prefix="/posts", tags=["posts"])


@router.post("", response_model=PostResponse, status_code=201)
async def create_post(
    req: CreatePostRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await post_svc.create_post(db, current_user.user_id, req)


@router.get("/feed", response_model=list[PostResponse])
async def list_posts_feed(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=50),
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    viewer_id = current_user.user_id if current_user else None
    return await post_svc.list_posts_feed(db, viewer_id, page, page_size)


@router.get("/by-user/{user_id}", response_model=list[PostResponse])
async def list_posts_by_user(
    user_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=50),
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    viewer_id = current_user.user_id if current_user else None
    return await post_svc.list_posts_by_user(db, user_id, viewer_id, page, page_size)


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: str,
    current_user: CurrentUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    viewer_id = current_user.user_id if current_user else None
    return await post_svc.get_post(db, post_id, viewer_id)


@router.delete("/{post_id}", status_code=204)
async def delete_post(
    post_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await post_svc.delete_post(db, post_id, current_user.user_id)


@router.post("/{post_id}/like", response_model=PostResponse)
async def like_post(
    post_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await post_svc.like_post(db, post_id, current_user.user_id)


@router.delete("/{post_id}/like", response_model=PostResponse)
async def unlike_post(
    post_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await post_svc.unlike_post(db, post_id, current_user.user_id)


@router.get("/{post_id}/comments", response_model=list[PostCommentResponse])
async def list_comments(
    post_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await post_svc.list_comments(db, post_id, page, page_size)


@router.post("/{post_id}/comments", response_model=PostCommentResponse, status_code=201)
async def add_comment(
    post_id: str,
    req: CreateCommentRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await post_svc.add_comment(db, post_id, current_user.user_id, req.content)


@router.delete("/{post_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    post_id: str,
    comment_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await post_svc.delete_comment(db, comment_id, current_user.user_id)


@router.post("/{post_id}/share", response_model=PostResponse)
async def share_post(
    post_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await post_svc.record_share(db, post_id, current_user.user_id)
