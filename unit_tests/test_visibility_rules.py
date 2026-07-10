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
async def test_discover_feed_applies_plan_type_filter():
    """planType was accepted into DiscoverFilters and threaded through the
    API/service signature but never actually applied to the query — the
    'User Plans' vs 'Corporate Plans' tabs an agency sees on Discover
    returned identical, unfiltered results. Confirms the WHERE clause now
    includes planType when the filter is set."""
    from app.schemas.discover import DiscoverFilters
    from app.services.discover import get_discover_feed

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    with patch("app.services.discover.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.services.discover.set_cached", new=AsyncMock()):
        await get_discover_feed(
            db, DiscoverFilters(page=1, page_size=20, plan_type="CORPORATE"), requesting_agency_id="agency-1"
        )

    executed_sql = [str(call.args[0]) for call in db.execute.call_args_list]
    assert any('"planType"' in sql for sql in executed_sql), (
        "plan_type filter was set on DiscoverFilters but never reached the WHERE clause"
    )


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


# ── Rule 4: Corporate plans are private to the creator + bidding agencies ──

def _fake_agency_current_user() -> CurrentUser:
    user = CurrentUser.__new__(CurrentUser)
    user.payload = {"sub": "agency-owner-1", "role": "agency_admin", "agencyId": "agency-1"}
    return user


@pytest.mark.asyncio
async def test_discover_feed_excludes_corporate_plans_for_non_agency_requester():
    from app.schemas.discover import DiscoverFilters
    from app.services.discover import get_discover_feed

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    with patch("app.services.discover.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.services.discover.set_cached", new=AsyncMock()):
        await get_discover_feed(db, DiscoverFilters(page=1, page_size=20), requesting_agency_id=None)

    executed_sql = [
        str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
        for call in db.execute.call_args_list
    ]
    assert any('"planType" = \'STANDARD\'' in sql for sql in executed_sql), (
        "non-agency Discover request must be forced to STANDARD plans only"
    )


@pytest.mark.asyncio
async def test_discover_feed_lets_agency_choose_corporate_plan_type():
    """A non-agency requester's plan_type filter is overridden to STANDARD
    (test above); an agency requester's own filter choice must still apply."""
    from app.schemas.discover import DiscoverFilters
    from app.services.discover import get_discover_feed

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    with patch("app.services.discover.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.services.discover.set_cached", new=AsyncMock()):
        await get_discover_feed(
            db, DiscoverFilters(page=1, page_size=20, plan_type="CORPORATE"), requesting_agency_id="agency-1"
        )

    executed_sql = [
        str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
        for call in db.execute.call_args_list
    ]
    assert any('"planType" = \'CORPORATE\'' in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_search_excludes_corporate_plans_for_non_agency_requester():
    from app.services.discover import search

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_empty_result())

    await search(db, "offsite", page=1, page_size=20, requesting_agency_id=None)

    executed_sql = [
        str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
        for call in db.execute.call_args_list
    ]
    assert any('"planType" = \'STANDARD\'' in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_outsider_cannot_join_a_corporate_plan_group():
    from app.api.v1.groups import join_group

    group = SimpleNamespace(id="group-1", plan_id="plan-1", package_id=None, is_locked=False)
    plan = SimpleNamespace(id="plan-1", plan_type="CORPORATE")
    db = AsyncMock()
    # lookup order: the group, existing membership (None — a stranger), the plan
    db.scalar = AsyncMock(side_effect=[group, None, plan])

    with pytest.raises(ForbiddenError, match="private"):
        await join_group("group-1", current_user=_fake_current_user(UserRole.USER), db=db)


@pytest.mark.asyncio
async def test_creator_can_still_reach_their_own_corporate_plan_group():
    """The creator already holds an active membership (added by accept_offer),
    so the early-return path returns it without ever reaching the corporate
    block — confirms the guard only stops strangers, not the organizer."""
    from app.api.v1.groups import join_group

    group = SimpleNamespace(id="group-1", plan_id="plan-1", package_id=None, is_locked=False)
    existing_member = SimpleNamespace(
        id="member-1", user_id="user-1", role="CREATOR", status="APPROVED", joined_at=None,
    )
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[group, existing_member])
    db.get = AsyncMock(return_value=SimpleNamespace(
        id="user-1", display_name="Creator", username="creator", avatar_url=None,
        verification_tier="BASIC", gender=None, location=None, avg_rating=0.0, completed_trips=0,
    ))

    result = await join_group("group-1", current_user=_fake_current_user(UserRole.USER), db=db)
    assert result.status == "APPROVED"


@pytest.mark.asyncio
async def test_agency_cannot_create_corporate_plan():
    from app.api.v1.plans import create
    from app.schemas.plans import CreatePlanRequest

    req = CreatePlanRequest(title="Offsite", destination="Goa", plan_type="CORPORATE")

    with pytest.raises(ForbiddenError, match="Agency accounts cannot create corporate"):
        await create(req, current_user=_fake_agency_current_user(), db=AsyncMock())


@pytest.mark.asyncio
async def test_traveler_can_create_corporate_plan():
    from app.api.v1.plans import create

    with patch("app.services.plans.create_plan", new=AsyncMock(return_value="created")) as mock_create:
        from app.schemas.plans import CreatePlanRequest
        req = CreatePlanRequest(title="Offsite", destination="Goa", plan_type="CORPORATE")
        result = await create(req, current_user=_fake_current_user(UserRole.USER), db=AsyncMock())

    assert result == "created"
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_corporate_open_listing_requires_agency():
    from app.api.v1.plans import list_corporate_open

    with pytest.raises(ForbiddenError):
        await list_corporate_open(page=1, page_size=20, current_user=_fake_current_user(UserRole.USER), db=AsyncMock())
