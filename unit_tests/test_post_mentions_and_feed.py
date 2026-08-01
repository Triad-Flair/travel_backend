from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.posts import CreatePostRequest
from app.services.posts import create_post, list_posts_feed


def _fake_user(**overrides):
    defaults = dict(
        id="user-1",
        username="isha_verma",
        display_name="Isha Verma",
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
async def test_create_post_notifies_mentioned_users_but_not_self():
    author = _fake_user(id="author-1", username="isha_verma")
    mentioned = _fake_user(id="mentioned-1", username="neil_thapa", display_name="Neil Thapa")

    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, id: {
        "author-1": author,
        "mentioned-1": mentioned,
    }.get(id))
    # Both @-mentioned usernames resolve, including the author themselves —
    # proves the self-mention is actually filtered out, not just absent.
    mention_result = MagicMock()
    mention_result.scalars.return_value.all.return_value = [mentioned, author]
    db.execute = AsyncMock(return_value=mention_result)
    db.scalar = AsyncMock(return_value=None)  # liked_by_me check inside _post_to_response

    req = CreatePostRequest(caption="Amazing trek with @neil_thapa and @isha_verma!", image_urls=[])

    with patch("app.services.posts.notif_svc.create_notification", new=AsyncMock()) as mock_notify:
        await create_post(db, "author-1", req)

    # Only the mentioned user (not the self-mentioning author) gets notified.
    mock_notify.assert_awaited_once()
    assert mock_notify.call_args.args[1] == "mentioned-1"
    assert mock_notify.call_args.args[2] == "POST_MENTION"


@pytest.mark.asyncio
async def test_create_post_skips_mention_lookup_when_no_at_tokens():
    author = _fake_user(id="author-1")
    db = AsyncMock()
    db.get = AsyncMock(return_value=author)
    db.scalar = AsyncMock(return_value=None)

    req = CreatePostRequest(caption="No mentions here, just a great sunset.", image_urls=[])

    with patch("app.services.posts.notif_svc.create_notification", new=AsyncMock()) as mock_notify:
        await create_post(db, "author-1", req)

    mock_notify.assert_not_awaited()
    db.execute.assert_not_awaited()  # mention lookup query never issued


@pytest.mark.asyncio
async def test_list_posts_feed_orders_and_paginates_across_all_authors():
    post1 = SimpleNamespace(
        id="post-1", author_user_id="a1", caption="One", image_urls=[], destination=None,
        like_count=0, comment_count=0, share_count=0, created_at=None,
    )
    post2 = SimpleNamespace(
        id="post-2", author_user_id="a2", caption="Two", image_urls=[], destination=None,
        like_count=0, comment_count=0, share_count=0, created_at=None,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [post1, post2]
    db.execute = AsyncMock(return_value=result)
    db.get = AsyncMock(side_effect=lambda model, id: {"a1": _fake_user(id="a1"), "a2": _fake_user(id="a2")}.get(id))
    db.scalar = AsyncMock(return_value=None)

    items = await list_posts_feed(db, viewer_user_id=None, page=1, page_size=12)

    assert [p.id for p in items] == ["post-1", "post-2"]
