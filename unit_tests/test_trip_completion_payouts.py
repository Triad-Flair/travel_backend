"""The agency's full payout now goes out at booking confirmation (see
test_agency_payout_auto_release.py), not on trip completion. complete_trip
still routes through execute_agency_payout as a safety net for any payment
whose confirmation-time payout never went out — it queries only payments
with payout_released=False, so a normally-paid trip triggers zero payout
calls here.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payments import complete_trip


def _fake_group(**overrides):
    defaults = dict(id="group-1", plan_id="plan-1", package_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_plan(**overrides):
    defaults = dict(id="plan-1", status="CONFIRMED")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_payment(**overrides):
    defaults = dict(id="payment-1", status="CAPTURED", payout_released=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _db_with(group=None, plan=None, package=None, unpaid_payments=None):
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[group, plan, package])
    result = MagicMock()
    result.scalars.return_value.all.return_value = unpaid_payments or []
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_complete_trip_marks_plan_completed():
    group = _fake_group()
    plan = _fake_plan()
    db = _db_with(group=group, plan=plan, unpaid_payments=[])

    await complete_trip(db, "group-1")

    assert plan.status == "COMPLETED"


@pytest.mark.asyncio
async def test_complete_trip_pays_out_any_payment_still_missing_its_payout():
    """Safety net: a payment whose confirmation-time payout never fired
    (e.g. a Route transfer failure) still gets paid when its trip completes."""
    group = _fake_group()
    plan = _fake_plan()
    payment = _fake_payment()
    db = _db_with(group=group, plan=plan, unpaid_payments=[payment])

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock()) as mock_payout:
        result = await complete_trip(db, "group-1")

    mock_payout.assert_called_once_with(db, "payment-1")
    assert result["payoutFailures"] == []


@pytest.mark.asyncio
async def test_complete_trip_does_not_touch_already_paid_out_payments():
    """The query itself filters to payout_released == False; this documents
    that complete_trip only acts on what that query returns."""
    group = _fake_group()
    plan = _fake_plan()
    db = _db_with(group=group, plan=plan, unpaid_payments=[])

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock()) as mock_payout:
        await complete_trip(db, "group-1")

    mock_payout.assert_not_called()


@pytest.mark.asyncio
async def test_complete_trip_continues_and_reports_after_payout_failure():
    """One payment's transfer failing must not block marking the trip
    completed, and must not silently swallow the failure either."""
    group = _fake_group()
    plan = _fake_plan()
    payment_a = _fake_payment(id="payment-a")
    payment_b = _fake_payment(id="payment-b")
    db = _db_with(group=group, plan=plan, unpaid_payments=[payment_a, payment_b])

    async def _payout_side_effect(_db, payment_id):
        if payment_id == "payment-a":
            raise Exception("Razorpay transfer failed")

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock(side_effect=_payout_side_effect)):
        result = await complete_trip(db, "group-1")

    assert plan.status == "COMPLETED"
    assert result["payoutFailures"] == ["payment-a"]
