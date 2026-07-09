"""PRD 2.3 authorization rules:
  1. Agency Discover queries never see other agencies' Packages.
  2. Agencies can't access group chats they don't own.
  3. Refer & Earn is blocked for agency accounts.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dependencies import CurrentUser
from app.exceptions import ForbiddenError
from app.models.enums import UserRole


def _empty_result():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


# ── Rule 1: Discover query-level package exclusion for agencies ────────────

@pytest.mark.asyncio
async def test_discover_feed_never_issues_a_package_query_for_agency_requester():
    from app.schemas.discover import DiscoverFilters
    from app.services.discover import get_discover_feed

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    with patch("app.services.discover.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.services.discover.set_cached", new=AsyncMock()):
        await get_discover_feed(db, DiscoverFilters(page=1, page_size=20), requesting_agency_id="agency-1")

    executed_sql = [str(call.args[0]) for call in db.execute.call_args_list]
    assert not any("packages" in sql.lower() for sql in executed_sql), (
        "agency requester triggered a packages query — the filter must be at the query level"
    )


@pytest.mark.asyncio
async def test_discover_feed_does_query_packages_for_a_public_requester():
    """Confirms the test above is meaningful — packages are queried when
    there's no requesting_agency_id."""
    from app.schemas.discover import DiscoverFilters
    from app.services.discover import get_discover_feed

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    with patch("app.services.discover.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.services.discover.set_cached", new=AsyncMock()):
        await get_discover_feed(db, DiscoverFilters(page=1, page_size=20), requesting_agency_id=None)

    executed_sql = [str(call.args[0]) for call in db.execute.call_args_list]
    assert any("packages" in sql.lower() for sql in executed_sql)


@pytest.mark.asyncio
async def test_trending_returns_empty_for_agency_without_querying_db():
    from app.services.discover import get_trending

    db = AsyncMock()
    result = await get_trending(db, page=1, page_size=20, requesting_agency_id="agency-1")

    assert result == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_search_never_issues_a_package_query_for_agency_requester():
    from app.services.discover import search

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    await search(db, "goa", page=1, page_size=20, requesting_agency_id="agency-1")

    executed_sql = [str(call.args[0]) for call in db.execute.call_args_list]
    assert not any("packages" in sql.lower() for sql in executed_sql)


# ── Rule 2: agencies can't access group chats they don't own ───────────────

@pytest.mark.asyncio
async def test_unrelated_agency_denied_group_chat_access():
    from app.services.chat import _assert_group_chat_access

    db = AsyncMock()
    # lookup order in _assert_group_chat_access: membership (None — not a
    # member), group, agency-by-owner (found — caller does own an agency,
    # just not one connected to this group), then no plan/package match.
    db.scalar = AsyncMock(side_effect=[
        None,  # not an existing GroupMember
        SimpleNamespace(id="group-1", plan_id=None, package_id=None),  # the group
        SimpleNamespace(id="some-other-agency"),  # caller's own agency
    ])

    with pytest.raises(ForbiddenError):
        await _assert_group_chat_access(db, "group-1", "agency-owner-user-id")


@pytest.mark.asyncio
async def test_agency_with_accepted_offer_is_granted_group_chat_access():
    from app.services.chat import _assert_group_chat_access

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[
        None,  # not an existing GroupMember
        SimpleNamespace(id="group-1", plan_id="plan-1", package_id=None),  # the group
        SimpleNamespace(id="agency-1"),  # caller's own agency
        SimpleNamespace(id="plan-1", status="CONFIRMED"),  # the plan
        SimpleNamespace(id="offer-1"),  # this agency's accepted offer on the plan
    ])

    await _assert_group_chat_access(db, "group-1", "agency-owner-user-id")  # should not raise


# ── Rule 3: Refer & Earn blocked for agency accounts ────────────────────────

def _fake_current_user(role: UserRole) -> CurrentUser:
    user = CurrentUser.__new__(CurrentUser)
    user.payload = {"sub": "user-1", "role": role.value}
    return user


def test_block_agencies_rejects_agency_admin():
    from app.api.v1.loyalty import _block_agencies

    with pytest.raises(ForbiddenError, match="not available for agency accounts"):
        _block_agencies(_fake_current_user(UserRole.AGENCY_ADMIN))


def test_block_agencies_allows_traveler():
    from app.api.v1.loyalty import _block_agencies

    _block_agencies(_fake_current_user(UserRole.USER))  # should not raise


def test_block_agencies_allows_platform_admin():
    from app.api.v1.loyalty import _block_agencies

    _block_agencies(_fake_current_user(UserRole.PLATFORM_ADMIN))  # should not raise
