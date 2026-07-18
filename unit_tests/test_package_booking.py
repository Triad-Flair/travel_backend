from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.group import Group, GroupMember
from app.services.packages import book_package
from app.services.plans import confirm_plan_with_offer


def _fake_package(**overrides):
    defaults = dict(id="pkg-1", slug="pkg-1-slug", title="Goa Getaway", status="OPEN", group_size_max=14)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_group(**overrides):
    defaults = dict(id="group-1", package_id="pkg-1", plan_id=None, current_size=1,
                     male_count=0, female_count=0, other_count=0, is_locked=False,
                     payment_window_closes_at=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_book_package_creates_new_group_when_none_exists():
    pkg = _fake_package()
    db = AsyncMock()
    # lookup order: package, existing group (None), existing member (None)
    db.scalar = AsyncMock(side_effect=[pkg, None, None])

    with patch("app.workers.tasks.send_group_chat_verification_email_task.delay"):
        result = await book_package(db, "pkg-1", "user-1")

    added = [call.args[0] for call in db.add.call_args_list]
    groups = [a for a in added if isinstance(a, Group)]
    members = [a for a in added if isinstance(a, GroupMember)]

    assert len(groups) == 1
    assert groups[0].package_id == "pkg-1"
    assert len(members) == 1
    assert members[0].role == "CREATOR"  # first member of a brand-new group
    assert members[0].status == "APPROVED"
    assert result.package_id == "pkg-1"


@pytest.mark.asyncio
async def test_book_package_joins_existing_group_as_member_not_creator():
    pkg = _fake_package()
    existing_group = _fake_group(current_size=2)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[pkg, existing_group, None])

    with patch("app.workers.tasks.send_group_chat_verification_email_task.delay"):
        await book_package(db, "pkg-1", "user-2")

    added = [call.args[0] for call in db.add.call_args_list]
    members = [a for a in added if isinstance(a, GroupMember)]
    assert len(members) == 1
    assert members[0].role == "MEMBER"  # joining a group that already has members
    assert existing_group.current_size == 3


@pytest.mark.asyncio
async def test_book_package_rejects_non_open_package():
    pkg = _fake_package(status="DRAFT")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=pkg)

    with pytest.raises(BadRequestError, match="not currently open"):
        await book_package(db, "pkg-1", "user-1")


@pytest.mark.asyncio
async def test_book_package_allows_joining_a_confirming_package():
    """Confirmed live: _finalize_capture flips a package OPEN -> CONFIRMING
    the instant its first traveler pays, long before group_size_min is
    met — requiring status == OPEN here meant every package became
    permanently unbookable for every traveler after the first (a real
    package sitting at 1/14 showed "This package is not currently open
    for booking" to everyone else)."""
    pkg = _fake_package(status="CONFIRMING")
    existing_group = _fake_group(current_size=1)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[pkg, existing_group, None])

    with patch("app.workers.tasks.send_group_chat_verification_email_task.delay"):
        await book_package(db, "pkg-1", "user-2")

    assert existing_group.current_size == 2


@pytest.mark.asyncio
async def test_book_package_rejects_when_group_is_already_full():
    """The status check alone used to be the only thing preventing
    overbooking; relaxing it to allow CONFIRMING needs its own explicit
    capacity guard."""
    pkg = _fake_package(status="CONFIRMING", group_size_max=14)
    full_group = _fake_group(current_size=14)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[pkg, full_group, None])

    with pytest.raises(BadRequestError, match="full"):
        await book_package(db, "pkg-1", "user-15")


@pytest.mark.asyncio
async def test_book_package_rejects_locked_group():
    pkg = _fake_package()
    locked_group = _fake_group(is_locked=True)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[pkg, locked_group])

    with pytest.raises(BadRequestError, match="locked"):
        await book_package(db, "pkg-1", "user-1")


@pytest.mark.asyncio
async def test_book_package_is_idempotent_for_already_active_member():
    pkg = _fake_package()
    group = _fake_group()
    active_member = SimpleNamespace(status="APPROVED")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[pkg, group, active_member])

    await book_package(db, "pkg-1", "user-1")
    db.add.assert_not_called()  # already an active member — no new row, no size bump


@pytest.mark.asyncio
async def test_book_package_raises_not_found_for_unknown_package():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    with pytest.raises(NotFoundError):
        await book_package(db, "nonexistent", "user-1")


@pytest.mark.asyncio
async def test_confirm_plan_with_offer_rejects_mismatched_offer():
    plan = SimpleNamespace(id="plan-1", creator_id="user-1", confirmed_offer_id="offer-1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=plan)

    with pytest.raises(BadRequestError, match="not the plan's accepted offer"):
        await confirm_plan_with_offer(db, "plan-1", "user-1", "offer-WRONG")


@pytest.mark.asyncio
async def test_confirm_plan_with_offer_rejects_non_creator():
    plan = SimpleNamespace(id="plan-1", creator_id="someone-else", confirmed_offer_id="offer-1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=plan)

    with pytest.raises(ForbiddenError):
        await confirm_plan_with_offer(db, "plan-1", "user-1", "offer-1")
