from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import ForbiddenError, NotFoundError
from app.services.posts import add_comment, delete_comment, like_post, unlike_post


def _fake_post(**overrides):
    defaults = dict(
        id="post-1",
        author_user_id="author-1",
        caption="Sunrise trek",
        image_urls=["data:image/png;base64,abc"],
        destination="Spiti Valley",
        like_count=0,
        comment_count=0,
        share_count=0,
        created_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_user(**overrides):
    defaults = dict(
        id="user-1",
        display_name="Isha Verma",
        username="isha_verma",
        avatar_url=None,
        verification_tier="VERIFIED",
        gender="female",
        location="Delhi",
        avg_rating=5.0,
        completed_trips=25,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_like_post_increments_count_and_notifies_owner():
    post = _fake_post()
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "post-1": post,
        "liker-1": _fake_user(id="liker-1", username="liker"),
        "author-1": _fake_user(id="author-1", username="author"),
    }.get(id))
    # 1st scalar call: "does a like already exist" check (no) — 2nd: the
    # liked_by_me lookup inside _post_to_response, after the like was added.
    db.scalar = AsyncMock(side_effect=[None, SimpleNamespace(id="like-1")])

    with patch("app.services.posts.notif_svc.create_notification", new=AsyncMock()) as mock_notify:
        result = await like_post(db, "post-1", "liker-1")

    assert post.like_count == 1
    assert result.liked_by_me is True
    mock_notify.assert_awaited_once()
    assert mock_notify.call_args.args[1] == "author-1"
    assert mock_notify.call_args.args[2] == "POST_LIKED"


@pytest.mark.asyncio
async def test_like_post_is_idempotent_when_already_liked():
    post = _fake_post(like_count=3)
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "post-1": post,
        "author-1": _fake_user(id="author-1", username="author"),
    }.get(id))
    db.scalar = AsyncMock(return_value=SimpleNamespace(id="like-1"))  # already liked

    with patch("app.services.posts.notif_svc.create_notification", new=AsyncMock()) as mock_notify:
        result = await like_post(db, "post-1", "liker-1")

    assert post.like_count == 3  # unchanged
    assert result.liked_by_me is True
    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_like_post_skips_notification_for_self_like():
    post = _fake_post(author_user_id="author-1")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "post-1": post,
        "author-1": _fake_user(id="author-1", username="author"),
    }.get(id))
    db.scalar = AsyncMock(return_value=None)

    with patch("app.services.posts.notif_svc.create_notification", new=AsyncMock()) as mock_notify:
        await like_post(db, "post-1", "author-1")

    assert post.like_count == 1
    mock_notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_unlike_post_decrements_but_never_goes_negative():
    post = _fake_post(like_count=0)
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "post-1": post,
        "author-1": _fake_user(id="author-1", username="author"),
    }.get(id))
    db.scalar = AsyncMock(return_value=None)  # no existing like to remove

    result = await unlike_post(db, "post-1", "liker-1")

    assert post.like_count == 0
    assert result.liked_by_me is False


@pytest.mark.asyncio
async def test_add_comment_increments_count_and_notifies_owner():
    post = _fake_post(comment_count=0)
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "post-1": post,
        "commenter-1": _fake_user(id="commenter-1", username="commenter"),
        "author-1": _fake_user(id="author-1", username="author"),
    }.get(id))

    with patch("app.services.posts.notif_svc.create_notification", new=AsyncMock()) as mock_notify:
        comment = await add_comment(db, "post-1", "commenter-1", "Beautiful shot!")

    assert post.comment_count == 1
    assert comment.content == "Beautiful shot!"
    mock_notify.assert_awaited_once()
    assert mock_notify.call_args.args[2] == "POST_COMMENTED"


@pytest.mark.asyncio
async def test_delete_comment_allows_post_owner_even_if_not_comment_author():
    post = _fake_post(author_user_id="post-owner", comment_count=1)
    comment = SimpleNamespace(id="c-1", post_id="post-1", author_user_id="someone-else")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "c-1": comment,
        "post-1": post,
    }.get(id))

    await delete_comment(db, "c-1", "post-owner")

    db.delete.assert_awaited_once_with(comment)
    assert post.comment_count == 0


@pytest.mark.asyncio
async def test_delete_comment_rejects_non_owner():
    post = _fake_post(author_user_id="post-owner")
    comment = SimpleNamespace(id="c-1", post_id="post-1", author_user_id="commenter-1")
    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "c-1": comment,
        "post-1": post,
    }.get(id))

    with pytest.raises(ForbiddenError):
        await delete_comment(db, "c-1", "random-user")


@pytest.mark.asyncio
async def test_like_post_raises_not_found_for_missing_post():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await like_post(db, "missing-post", "user-1")
