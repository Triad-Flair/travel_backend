"""Confirmed live: a package sitting at 2/2 required captured travelers
(group_size_min=2, both COMMITTED) never flipped to CONFIRMED and never
released tranche1 — the real-time check inside _finalize_capture silently
missed it. Re-running the identical computation against the same data
afterward correctly found it confirmable, so the check itself is sound;
this reads as a one-off transaction-timing issue. check_and_confirm_group
extracts that check into a function reusable both in the real-time path
and from a periodic safety-net task (reconcile_stuck_group_confirmations)
that re-scans every CONFIRMING plan/package.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payments import check_and_confirm_group


def _fake_group(**overrides):
    defaults = dict(id="group-1", plan_id=None, package_id="pkg-1")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_package(**overrides):
    defaults = dict(id="pkg-1", status="CONFIRMING", group_size_min=2)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_member(**overrides):
    defaults = dict(user_id="user-1", status="COMMITTED")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_confirms_package_and_releases_tranche1_when_fully_captured():
    group = _fake_group()
    package = _fake_package(group_size_min=2)
    members = [_fake_member(user_id="user-1"), _fake_member(user_id="user-2")]

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = members
    db.execute = AsyncMock(return_value=execute_result)
    # captured_count query, then package lookup.
    db.scalar = AsyncMock(side_effect=[2, package])

    with patch("app.services.payments._release_tranche1_for_group", new=AsyncMock()) as mock_release:
        confirmed = await check_and_confirm_group(db, group)

    assert confirmed is True
    assert package.status == "CONFIRMED"
    mock_release.assert_awaited_once_with(db, "group-1", ["user-1", "user-2"])


@pytest.mark.asyncio
async def test_is_idempotent_for_already_confirmed_package():
    group = _fake_group()
    package = _fake_package(status="CONFIRMED", group_size_min=2)
    members = [_fake_member(user_id="user-1"), _fake_member(user_id="user-2")]

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = members
    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock(side_effect=[2, package])

    with patch("app.services.payments._release_tranche1_for_group", new=AsyncMock()) as mock_release:
        confirmed = await check_and_confirm_group(db, group)

    assert confirmed is False
    mock_release.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_confirm_when_below_minimum_travelers():
    group = _fake_group()
    package = _fake_package(status="CONFIRMING", group_size_min=4)
    members = [_fake_member(user_id="user-1"), _fake_member(user_id="user-2")]

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = members
    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock(side_effect=[2, package])

    with patch("app.services.payments._release_tranche1_for_group", new=AsyncMock()) as mock_release:
        confirmed = await check_and_confirm_group(db, group)

    assert confirmed is False
    assert package.status == "CONFIRMING"
    mock_release.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_confirm_when_a_member_has_not_yet_paid():
    """captured_count must equal the full active member count, not just
    meet group_size_min — a 3rd member who joined but hasn't paid yet
    must not trigger confirmation."""
    group = _fake_group()
    package = _fake_package(status="CONFIRMING", group_size_min=2)
    members = [_fake_member(user_id="user-1"), _fake_member(user_id="user-2"), _fake_member(user_id="user-3", status="APPROVED")]

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = members
    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock(side_effect=[2, package])  # only 2 of 3 active members captured

    with patch("app.services.payments._release_tranche1_for_group", new=AsyncMock()) as mock_release:
        confirmed = await check_and_confirm_group(db, group)

    assert confirmed is False
    mock_release.assert_not_called()


@pytest.mark.asyncio
async def test_no_active_members_is_a_noop():
    group = _fake_group()
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock()

    confirmed = await check_and_confirm_group(db, group)

    assert confirmed is False
    db.scalar.assert_not_called()
