import base64
import binascii
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models.social import Follow, Post, PostComment, PostCommentLike, PostLike, PostReport, PostSave, UserBlock
from app.models.user import User
from app.schemas.common import UserSummary
from app.schemas.posts import (
    MAX_POST_IMAGES,
    CreatePostReportRequest,
    CreatePostRequest,
    PostCommentResponse,
    PostFeedPageResponse,
    PostReportResponse,
    PostResponse,
)
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


def _follow_target_is_user():
    return cast(Follow.target_type, String) == "USER"


def _author_is_followed(viewer_user_id: str):
    return (
        select(Follow.id)
        .where(
            Follow.follower_user_id == viewer_user_id,
            _follow_target_is_user(),
            Follow.target_user_id == Post.author_user_id,
        )
        .exists()
    )


def _visible_post_filters(viewer_user_id: str | None):
    if not viewer_user_id:
        return []
    blocked_author_ids = select(UserBlock.blocked_user_id).where(UserBlock.blocker_user_id == viewer_user_id)
    return [~Post.author_user_id.in_(blocked_author_ids)]


def _encode_cursor(created_at: datetime, post_id: str, priority: int | None = None) -> str:
    payload: dict[str, str | int] = {"createdAt": _iso(created_at), "id": post_id}
    if priority is not None:
        payload["priority"] = priority
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str, int | None]:
    try:
        raw = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw).decode())
        created_at = datetime.fromisoformat(str(payload["createdAt"]).replace("Z", "+00:00"))
        post_id = str(payload["id"])
        priority = int(payload["priority"]) if "priority" in payload else None
        return created_at, post_id, priority
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid feed cursor") from exc


async def _post_to_response(db: AsyncSession, post: Post, viewer_user_id: str | None) -> PostResponse:
    author = await db.get(User, post.author_user_id)
    liked_by_me = False
    if viewer_user_id:
        like = await db.scalar(
            select(PostLike).where(PostLike.post_id == post.id, PostLike.user_id == viewer_user_id)
        )
        liked_by_me = like is not None
    saved_by_me = False
    author_followed_by_me = False
    if viewer_user_id:
        saved_by_me = bool(
            await db.scalar(select(PostSave.id).where(PostSave.post_id == post.id, PostSave.user_id == viewer_user_id))
        )
        author_followed_by_me = bool(
            await db.scalar(
                select(Follow.id).where(
                    Follow.follower_user_id == viewer_user_id,
                    _follow_target_is_user(),
                    Follow.target_user_id == post.author_user_id,
                )
            )
        )
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
        saved_by_me=saved_by_me,
        author_followed_by_me=author_followed_by_me,
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
    query = select(Post).where(*_visible_post_filters(viewer_user_id))
    if viewer_user_id:
        follows_author = _author_is_followed(viewer_user_id)
        # Priority is applied before pagination, so page one starts with the
        # newest posts from creators the viewer follows.
        query = query.order_by(case((follows_author, 0), else_=1), Post.created_at.desc())
    else:
        query = query.order_by(Post.created_at.desc())

    rows = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
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
        .where(Post.author_user_id == target_user_id, *_visible_post_filters(viewer_user_id))
        .order_by(Post.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    posts = rows.scalars().all()
    return [await _post_to_response(db, post, viewer_user_id) for post in posts]


async def _cursor_page(
    db: AsyncSession,
    query,
    viewer_user_id: str | None,
    limit: int,
    cursor: str | None,
    priority_expression=None,
) -> PostFeedPageResponse:
    if cursor:
        created_at, post_id, cursor_priority = _decode_cursor(cursor)
        chronological_after = or_(
            Post.created_at < created_at,
            and_(Post.created_at == created_at, Post.id < post_id),
        )
        if priority_expression is not None:
            if cursor_priority is None:
                raise ValidationError("Invalid feed cursor")
            query = query.where(
                or_(
                    priority_expression > cursor_priority,
                    and_(priority_expression == cursor_priority, chronological_after),
                )
            )
        else:
            query = query.where(chronological_after)

    rows = await db.execute(query.limit(limit + 1))
    posts = list(rows.scalars().all())
    has_more = len(posts) > limit
    page_posts = posts[:limit]
    items = [await _post_to_response(db, post, viewer_user_id) for post in page_posts]
    next_cursor = None
    if has_more and page_posts:
        last_post = page_posts[-1]
        priority = None
        if priority_expression is not None:
            priority = 0 if items[-1].author_followed_by_me else 1
        next_cursor = _encode_cursor(last_post.created_at, last_post.id, priority)
    return PostFeedPageResponse(items=items, next_cursor=next_cursor)


async def list_cursor_feed(
    db: AsyncSession,
    viewer_user_id: str | None,
    limit: int,
    cursor: str | None,
) -> PostFeedPageResponse:
    query = select(Post).where(*_visible_post_filters(viewer_user_id))
    if viewer_user_id:
        follows_author = _author_is_followed(viewer_user_id)
        priority = case((follows_author, 0), else_=1)
        return await _cursor_page(
            db,
            query.order_by(priority, Post.created_at.desc(), Post.id.desc()),
            viewer_user_id,
            limit,
            cursor,
            priority,
        )
    return await _cursor_page(
        db,
        query.order_by(Post.created_at.desc(), Post.id.desc()),
        viewer_user_id,
        limit,
        cursor,
    )


async def list_following_posts(
    db: AsyncSession,
    viewer_user_id: str,
    limit: int,
    cursor: str | None,
) -> PostFeedPageResponse:
    follows_author = _author_is_followed(viewer_user_id)
    query = (
        select(Post)
        .where(follows_author, *_visible_post_filters(viewer_user_id))
        .order_by(Post.created_at.desc(), Post.id.desc())
    )
    return await _cursor_page(db, query, viewer_user_id, limit, cursor)


async def list_saved_posts(
    db: AsyncSession,
    viewer_user_id: str,
    limit: int,
    cursor: str | None,
) -> PostFeedPageResponse:
    query = (
        select(Post, PostSave.created_at.label("saved_at"))
        .join(PostSave, PostSave.post_id == Post.id)
        .where(PostSave.user_id == viewer_user_id, *_visible_post_filters(viewer_user_id))
        .order_by(PostSave.created_at.desc(), Post.id.desc())
    )
    if cursor:
        created_at, post_id, _ = _decode_cursor(cursor)
        query = query.where(or_(PostSave.created_at < created_at, and_(PostSave.created_at == created_at, Post.id < post_id)))
    rows = list((await db.execute(query.limit(limit + 1))).all())
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [await _post_to_response(db, post, viewer_user_id) for post, _ in page_rows]
    next_cursor = _encode_cursor(page_rows[-1].saved_at, page_rows[-1][0].id) if has_more and page_rows else None
    return PostFeedPageResponse(items=items, next_cursor=next_cursor)


async def list_trending_posts(db: AsyncSession, viewer_user_id: str | None, limit: int) -> list[PostResponse]:
    """Rank recent community posts by meaningful interaction, not all-time totals."""
    engagement = Post.like_count * 3 + Post.comment_count * 4 + Post.share_count * 5
    rows = await db.execute(
        select(Post)
        .where(Post.created_at >= datetime.now(UTC) - timedelta(days=30), *_visible_post_filters(viewer_user_id))
        .order_by(engagement.desc(), Post.created_at.desc(), Post.id.desc())
        .limit(limit)
    )
    return [await _post_to_response(db, post, viewer_user_id) for post in rows.scalars().all()]


async def save_post(db: AsyncSession, post_id: str, user_id: str) -> PostResponse:
    post = await db.scalar(select(Post).where(Post.id == post_id, *_visible_post_filters(user_id)))
    if not post:
        raise NotFoundError("Post")
    existing = await db.scalar(select(PostSave.id).where(PostSave.post_id == post_id, PostSave.user_id == user_id))
    if not existing:
        db.add(PostSave(id=str(uuid.uuid4()), post_id=post_id, user_id=user_id))
        await db.flush()
    return await _post_to_response(db, post, user_id)


async def unsave_post(db: AsyncSession, post_id: str, user_id: str) -> PostResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")
    existing = await db.scalar(select(PostSave).where(PostSave.post_id == post_id, PostSave.user_id == user_id))
    if existing:
        await db.delete(existing)
        await db.flush()
    return await _post_to_response(db, post, user_id)


async def report_post(
    db: AsyncSession,
    post_id: str,
    user_id: str,
    req: CreatePostReportRequest,
) -> PostReportResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")
    if post.author_user_id == user_id:
        raise ValidationError("You cannot report your own post")
    existing = await db.scalar(select(PostReport).where(PostReport.post_id == post_id, PostReport.reporter_user_id == user_id))
    if existing:
        raise ConflictError("You have already reported this post")
    report = PostReport(
        id=str(uuid.uuid4()),
        post_id=post_id,
        reporter_user_id=user_id,
        reason=req.reason,
        details=req.details.strip() if req.details else None,
    )
    db.add(report)
    await db.flush()
    return PostReportResponse(id=report.id, status=report.status, created_at=_iso(report.created_at))


async def get_post(db: AsyncSession, post_id: str, viewer_user_id: str | None) -> PostResponse:
    post = await db.scalar(select(Post).where(Post.id == post_id, *_visible_post_filters(viewer_user_id)))
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


async def _comment_to_response(
    db: AsyncSession,
    comment: PostComment,
    liked_comment_ids: set[str] | None = None,
) -> PostCommentResponse:
    author = await db.get(User, comment.author_user_id)
    return PostCommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        author=_user_summary(author),
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        like_count=int(comment.like_count or 0),
        liked_by_me=comment.id in (liked_comment_ids or set()),
        created_at=_iso(comment.created_at),
    )


async def add_comment(
    db: AsyncSession,
    post_id: str,
    user_id: str,
    content: str,
    parent_comment_id: str | None = None,
) -> PostCommentResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")

    parent_comment: PostComment | None = None
    if parent_comment_id:
        parent_comment = await db.get(PostComment, parent_comment_id)
        if not parent_comment or parent_comment.post_id != post_id:
            raise NotFoundError("Parent comment")
        if parent_comment.parent_comment_id:
            raise ValidationError("Replies can only be one level deep")

    comment = PostComment(
        id=str(uuid.uuid4()),
        post_id=post_id,
        author_user_id=user_id,
        parent_comment_id=parent_comment_id,
        content=content,
    )
    db.add(comment)
    post.comment_count = int(post.comment_count or 0) + 1
    await db.flush()

    commenter = await db.get(User, user_id)
    commenter_name = (commenter.display_name or commenter.username or "Someone") if commenter else "Someone"
    notification_user_id = parent_comment.author_user_id if parent_comment else post.author_user_id
    if notification_user_id != user_id:
        profile_owner = await db.get(User, notification_user_id)
        await notif_svc.create_notification(
            db,
            notification_user_id,
            "POST_COMMENT_REPLIED" if parent_comment else "POST_COMMENTED",
            title="New reply to your comment" if parent_comment else "New comment on your post",
            body=f"{commenter_name} replied to your comment." if parent_comment else f"{commenter_name} commented on your post.",
            href=f"/profile/{profile_owner.username}" if profile_owner and profile_owner.username else None,
            metadata={"postId": post.id, "actorId": user_id, "commentId": comment.id, "parentCommentId": parent_comment_id},
        )

    return await _comment_to_response(db, comment)


async def list_comments(
    db: AsyncSession,
    post_id: str,
    page: int,
    page_size: int,
    viewer_user_id: str | None = None,
) -> list[PostCommentResponse]:
    rows = await db.execute(
        select(PostComment)
        .where(PostComment.post_id == post_id)
        .order_by(PostComment.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    comments = rows.scalars().all()
    liked_comment_ids: set[str] = set()
    if viewer_user_id and comments:
        liked_rows = await db.execute(
            select(PostCommentLike.comment_id).where(
                PostCommentLike.user_id == viewer_user_id,
                PostCommentLike.comment_id.in_([comment.id for comment in comments]),
            )
        )
        liked_comment_ids = set(liked_rows.scalars().all())
    return [await _comment_to_response(db, c, liked_comment_ids) for c in comments]


async def delete_comment(db: AsyncSession, comment_id: str, user_id: str) -> None:
    comment = await db.get(PostComment, comment_id)
    if not comment:
        raise NotFoundError("Comment")
    post = await db.get(Post, comment.post_id)
    is_owner_of_comment = comment.author_user_id == user_id
    is_owner_of_post = post and post.author_user_id == user_id
    if not (is_owner_of_comment or is_owner_of_post):
        raise ForbiddenError("Only the comment's author or the post's author can delete it")

    replies: list[PostComment] = []
    if not comment.parent_comment_id:
        reply_rows = await db.execute(select(PostComment).where(PostComment.parent_comment_id == comment.id))
        replies = list(reply_rows.scalars().all())
        for reply in replies:
            await db.delete(reply)
    await db.delete(comment)
    if post:
        post.comment_count = max(0, int(post.comment_count or 0) - 1 - len(replies))
    await db.flush()


async def _get_comment_for_post(db: AsyncSession, post_id: str, comment_id: str) -> PostComment:
    comment = await db.get(PostComment, comment_id)
    if not comment or comment.post_id != post_id:
        raise NotFoundError("Comment")
    return comment


async def like_comment(db: AsyncSession, post_id: str, comment_id: str, user_id: str) -> PostCommentResponse:
    comment = await _get_comment_for_post(db, post_id, comment_id)
    existing = await db.scalar(
        select(PostCommentLike).where(PostCommentLike.comment_id == comment_id, PostCommentLike.user_id == user_id)
    )
    if not existing:
        db.add(PostCommentLike(id=str(uuid.uuid4()), comment_id=comment_id, user_id=user_id))
        comment.like_count = int(comment.like_count or 0) + 1
        await db.flush()
    return await _comment_to_response(db, comment, {comment_id})


async def unlike_comment(db: AsyncSession, post_id: str, comment_id: str, user_id: str) -> PostCommentResponse:
    comment = await _get_comment_for_post(db, post_id, comment_id)
    existing = await db.scalar(
        select(PostCommentLike).where(PostCommentLike.comment_id == comment_id, PostCommentLike.user_id == user_id)
    )
    if existing:
        await db.delete(existing)
        comment.like_count = max(0, int(comment.like_count or 0) - 1)
        await db.flush()
    return await _comment_to_response(db, comment)


async def record_share(db: AsyncSession, post_id: str, user_id: str) -> PostResponse:
    post = await db.get(Post, post_id)
    if not post:
        raise NotFoundError("Post")
    post.share_count = int(post.share_count or 0) + 1
    await db.flush()
    return await _post_to_response(db, post, user_id)
