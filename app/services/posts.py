import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ForbiddenError, NotFoundError
from app.models.social import Post, PostComment, PostLike
from app.models.user import User
from app.schemas.common import UserSummary
from app.schemas.posts import MAX_POST_IMAGES, CreatePostRequest, PostCommentResponse, PostResponse
from app.services import notifications as notif_svc

MENTION_PATTERN = re.compile(r"@([a-zA-Z0-9_]{3,50})")


def _user_summary(user: User | None) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(
        id=user.id,
        full_name=user.display_name or user.username or "",
        username=user.username,
        avatar_url=user.avatar_url,
        verification=user.verification_tier,
        gender=user.gender,
        city=user.location,
        avg_rating=user.avg_rating,
        completed_trips=user.completed_trips,
    )


def _iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat()


async def _post_to_response(db: AsyncSession, post: Post, viewer_user_id: str | None) -> PostResponse:
    author = await db.get(User, post.author_user_id)
    liked_by_me = False
    if viewer_user_id:
        like = await db.scalar(
            select(PostLike).where(PostLike.post_id == post.id, PostLike.user_id == viewer_user_id)
        )
        liked_by_me = like is not None
    return PostResponse(
        id=post.id,
        author=_user_summary(author),
        caption=post.caption,
        image_urls=post.image_urls or [],
        destination=post.destination,
        like_count=int(post.like_count or 0),
        comment_count=int(post.comment_count or 0),
        share_count=int(post.share_count or 0),
        liked_by_me=liked_by_me,
        created_at=_iso(post.created_at),
    )


async def _notify_mentions(db: AsyncSession, post: Post, author: User | None) -> None:
    """@username tokens in a post caption notify the mentioned users — usernames
    are always lowercase (signup enforces `^[a-z0-9_]+$`), so tokens are
    lowercased before lookup regardless of how they were typed/cased."""
    if not post.caption:
        return
    usernames = {m.group(1).lower() for m in MENTION_PATTERN.finditer(post.caption)}
    if not usernames:
        return

    rows = await db.execute(select(User).where(func.lower(User.username).in_(usernames)))
    mentioned_users = rows.scalars().all()
    author_name = (author.display_name or author.username or "Someone") if author else "Someone"

    for user in mentioned_users:
        if user.id == post.author_user_id:
            continue
        await notif_svc.create_notification(
            db,
            user.id,
            "POST_MENTION",
            title="You were mentioned in a post",
            body=f"{author_name} mentioned you in a post.",
            href=f"/profile/{author.username}" if author and author.username else None,
            metadata={"postId": post.id, "actorId": post.author_user_id},
        )


async def create_post(db: AsyncSession, user_id: str, req: CreatePostRequest) -> PostResponse:
    post = Post(
        id=str(uuid.uuid4()),
        author_user_id=user_id,
        caption=req.caption,
        image_urls=req.image_urls[:MAX_POST_IMAGES],
        destination=req.destination,
    )
    db.add(post)
    await db.flush()

    author = await db.get(User, user_id)
    await _notify_mentions(db, post, author)

    return await _post_to_response(db, post, user_id)


async def list_posts_feed(
    db: AsyncSession,
    viewer_user_id: str | None,
    page: int,
    page_size: int,
) -> list[PostResponse]:
    """Global feed across every user's posts, newest first — the "browse
    everyone's posts" counterpart to list_posts_by_user's single-profile view."""
    rows = await db.execute(
        select(Post)
        .order_by(Post.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    posts = rows.scalars().all()
    return [await _post_to_response(db, post, viewer_user_id) for post in posts]


async def list_posts_by_user(
    db: AsyncSession,
    target_user_id: str,
    viewer_user_id: str | None,
    page: int,
    page_size: int,
) -> list[PostResponse]:
    rows = await db.execute(
        select(Post)
        .where(Post.author_user_id == target_user_id)
        .order_by(Post.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    posts = rows.scalars().all()
    return [await _post_to_response(db, post, viewer_user_id) for post in posts]


async def get_post(db: AsyncSession, post_id: str, viewer_user_id: str | None) -> PostResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")
    return await _post_to_response(db, post, viewer_user_id)


async def delete_post(db: AsyncSession, post_id: str, user_id: str) -> None:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")
    if post.author_user_id != user_id:
        raise ForbiddenError("Only the post's author can delete it")
    await db.delete(post)
    await db.flush()


async def like_post(db: AsyncSession, post_id: str, user_id: str) -> PostResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")

    existing = await db.scalar(
        select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
    )
    if not existing:
        db.add(PostLike(id=str(uuid.uuid4()), post_id=post_id, user_id=user_id))
        post.like_count = int(post.like_count or 0) + 1
        await db.flush()

        if post.author_user_id != user_id:
            liker = await db.get(User, user_id)
            liker_name = (liker.display_name or liker.username or "Someone") if liker else "Someone"
            post_author = await db.get(User, post.author_user_id)
            await notif_svc.create_notification(
                db,
                post.author_user_id,
                "POST_LIKED",
                title="New like on your post",
                body=f"{liker_name} liked your post.",
                href=f"/profile/{post_author.username}" if post_author and post_author.username else None,
                metadata={"postId": post.id, "actorId": user_id},
            )

    return await _post_to_response(db, post, user_id)


async def unlike_post(db: AsyncSession, post_id: str, user_id: str) -> PostResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")

    existing = await db.scalar(
        select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
    )
    if existing:
        await db.delete(existing)
        post.like_count = max(0, int(post.like_count or 0) - 1)
        await db.flush()

    return await _post_to_response(db, post, user_id)


async def _comment_to_response(db: AsyncSession, comment: PostComment) -> PostCommentResponse:
    author = await db.get(User, comment.author_user_id)
    return PostCommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        author=_user_summary(author),
        content=comment.content,
        created_at=_iso(comment.created_at),
    )


async def add_comment(db: AsyncSession, post_id: str, user_id: str, content: str) -> PostCommentResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")

    comment = PostComment(id=str(uuid.uuid4()), post_id=post_id, author_user_id=user_id, content=content)
    db.add(comment)
    post.comment_count = int(post.comment_count or 0) + 1
    await db.flush()

    if post.author_user_id != user_id:
        commenter = await db.get(User, user_id)
        commenter_name = (commenter.display_name or commenter.username or "Someone") if commenter else "Someone"
        post_author = await db.get(User, post.author_user_id)
        await notif_svc.create_notification(
            db,
            post.author_user_id,
            "POST_COMMENTED",
            title="New comment on your post",
            body=f"{commenter_name} commented on your post.",
            href=f"/profile/{post_author.username}" if post_author and post_author.username else None,
            metadata={"postId": post.id, "actorId": user_id, "commentId": comment.id},
        )

    return await _comment_to_response(db, comment)


async def list_comments(db: AsyncSession, post_id: str, page: int, page_size: int) -> list[PostCommentResponse]:
    rows = await db.execute(
        select(PostComment)
        .where(PostComment.post_id == post_id)
        .order_by(PostComment.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    comments = rows.scalars().all()
    return [await _comment_to_response(db, c) for c in comments]


async def delete_comment(db: AsyncSession, comment_id: str, user_id: str) -> None:
    comment = await db.get(PostComment, comment_id)
    if not comment:
        raise NotFoundError("Comment")
    post = await db.get(Post, comment.post_id)
    is_owner_of_comment = comment.author_user_id == user_id
    is_owner_of_post = post and post.author_user_id == user_id
    if not (is_owner_of_comment or is_owner_of_post):
        raise ForbiddenError("Only the comment's author or the post's author can delete it")

    await db.delete(comment)
    if post:
        post.comment_count = max(0, int(post.comment_count or 0) - 1)
    await db.flush()


async def record_share(db: AsyncSession, post_id: str, user_id: str) -> PostResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")
    post.share_count = int(post.share_count or 0) + 1
    await db.flush()
    return await _post_to_response(db, post, user_id)
