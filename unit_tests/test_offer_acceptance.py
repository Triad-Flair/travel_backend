from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import BadRequestError, ForbiddenError
from app.models.group import Group, GroupMember
from app.models.package import Package
from app.services.offers import accept_offer, counter_offer, reject_offer, withdraw_offer


def _fake_offer(**overrides):
    defaults = dict(
        id="offer-1",
        plan_id="plan-1",
        agency_id="agency-1",
        price_per_person=5000,
        pricing_tiers=None,
        inclusions=None,
        itinerary=None,
        cancellation_policy=None,
        cancellation_rules=None,
        status="PENDING",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_plan(**overrides):
    defaults = dict(
        id="plan-1",
        creator_id="user-1",
        title="Weekend in Goa",
        destination="Goa",
        destination_state="Goa",
        start_date=None,
        end_date=None,
        group_size_min=2,
        group_size_max=6,
        accommodation=None,
        vibes=None,
        activities=None,
        confirmed_offer_id=None,
        status="OPEN",
        confirmed_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_accept_offer_creates_package_group_and_creator_membership():
    offer = _fake_offer(status="PENDING")
    plan = _fake_plan()

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[offer, plan])

    with patch("app.services.offers._offer_to_response", new=AsyncMock(return_value="response")), \
         patch("app.workers.tasks.send_group_chat_verification_email_task.delay"):
        result = await accept_offer(db, "offer-1", "user-1")

    assert result == "response"
    assert offer.status == "ACCEPTED"
    assert plan.status == "CONFIRMED"
    assert plan.confirmed_offer_id == "offer-1"

    added = [call.args[0] for call in db.add.call_args_list]
    packages = [a for a in added if isinstance(a, Package)]
    groups = [a for a in added if isinstance(a, Group)]
    members = [a for a in added if isinstance(a, GroupMember)]

    assert len(packages) == 1
    assert packages[0].agency_id == "agency-1"
    assert packages[0].price_per_person == 5000
    assert packages[0].source_offer_id == "offer-1"
    assert packages[0].status == "CONFIRMING"

    assert len(groups) == 1
    assert groups[0].plan_id == "plan-1"
    assert groups[0].package_id == packages[0].id
    assert groups[0].current_size == 1

    assert len(members) == 1
    assert members[0].user_id == "user-1"
    assert members[0].role == "CREATOR"
    # APPROVED, not COMMITTED — COMMITTED is reserved for a captured payment
    # (see services/payments.py::_finalize_capture).
    assert members[0].status == "APPROVED"


@pytest.mark.asyncio
async def test_accept_offer_rejects_non_plan_creator():
    offer = _fake_offer()
    plan = _fake_plan(creator_id="someone-else")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[offer, plan])

    with pytest.raises(ForbiddenError):
        await accept_offer(db, "offer-1", "user-1")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_accept_offer_rejects_already_accepted_offer():
    offer = _fake_offer(status="ACCEPTED")
    plan = _fake_plan()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[offer, plan])

    with pytest.raises(BadRequestError, match="no longer be accepted"):
        await accept_offer(db, "offer-1", "user-1")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_accept_offer_rejects_withdrawn_offer():
    offer = _fake_offer(status="WITHDRAWN")
    plan = _fake_plan()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[offer, plan])

    with pytest.raises(BadRequestError):
        await accept_offer(db, "offer-1", "user-1")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_reject_offer_rejects_already_terminal_offer():
    offer = _fake_offer(status="REJECTED")
    plan = _fake_plan()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[offer, plan])

    with pytest.raises(BadRequestError, match="no longer be rejected"):
        await reject_offer(db, "offer-1", "user-1", None)


@pytest.mark.asyncio
async def test_withdraw_offer_rejects_already_accepted_offer():
    offer = _fake_offer(status="ACCEPTED", agency_id="agency-1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=offer)

    with pytest.raises(BadRequestError, match="no longer be withdrawn"):
        await withdraw_offer(db, "offer-1", "agency-1")


@pytest.mark.asyncio
async def test_counter_offer_rejects_on_accepted_offer():
    offer = _fake_offer(status="ACCEPTED")
    plan = _fake_plan()
    db = AsyncMock()
    # counter_offer's lookup order: offer, plan, then _resolve_agency_id_for_user's
    # two scalar calls (owner lookup, member lookup) before reaching the status guard.
    db.scalar = AsyncMock(side_effect=[offer, plan, None, None])

    with pytest.raises((BadRequestError, ForbiddenError)):
        await counter_offer(db, "offer-1", "user-1", "user", req=SimpleNamespace(price=4500, inclusions_delta=None, message=None))
