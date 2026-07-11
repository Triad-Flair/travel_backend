"""complete_trip used to fake tranche2 settlement: it flipped
tranche2_released/escrowStatus/transferStatus directly and emailed the
agency a "final settlement" notice, without ever calling create_transfer or
crediting AgencyWallet — the only function that actually does either is
execute_agency_payout, which complete_trip bypassed entirely. Harmless only
because no agency has a linked Razorpay account yet; the moment one does,
agencies would be told they'd been paid when no money had moved.
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
    defaults = dict(
        id="payment-1", status="CAPTURED", tranche1_released=False, tranche2_released=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _db_with(group=None, plan=None, package=None, captured_payments=None):
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[group, plan, package])
    result = MagicMock()
    result.scalars.return_value.all.return_value = captured_payments or []
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_complete_trip_marks_plan_completed():
    group = _fake_group()
    plan = _fake_plan()
    db = _db_with(group=group, plan=plan, captured_payments=[])

    await complete_trip(db, "group-1")

    assert plan.status == "COMPLETED"


@pytest.mark.asyncio
async def test_complete_trip_routes_unreleased_tranches_through_execute_agency_payout():
    group = _fake_group()
    plan = _fake_plan()
    payment = _fake_payment()
    db = _db_with(group=group, plan=plan, captured_payments=[payment])

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock()) as mock_payout:
        result = await complete_trip(db, "group-1")

    mock_payout.assert_any_call(db, "payment-1", "tranche1")
    mock_payout.assert_any_call(db, "payment-1", "tranche2")
    assert mock_payout.call_count == 2
    assert result["payoutFailures"] == []


@pytest.mark.asyncio
async def test_complete_trip_skips_already_released_tranches():
    group = _fake_group()
    plan = _fake_plan()
    payment = _fake_payment(tranche1_released=True, tranche2_released=False)
    db = _db_with(group=group, plan=plan, captured_payments=[payment])

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock()) as mock_payout:
        await complete_trip(db, "group-1")

    mock_payout.assert_called_once_with(db, "payment-1", "tranche2")


@pytest.mark.asyncio
async def test_complete_trip_ignores_non_captured_payments():
    """A PENDING or FAILED payment has no money in escrow — it must never
    trigger a payout just because the trip finished."""
    group = _fake_group()
    plan = _fake_plan()
    # The query itself filters to status == CAPTURED; this test documents
    # that complete_trip only acts on what that query returns.
    db = _db_with(group=group, plan=plan, captured_payments=[])

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
    db = _db_with(group=group, plan=plan, captured_payments=[payment_a, payment_b])

    async def _payout_side_effect(_db, payment_id, tranche):
        if payment_id == "payment-a":
            raise Exception("Razorpay transfer failed")

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock(side_effect=_payout_side_effect)):
        result = await complete_trip(db, "group-1")

    assert plan.status == "COMPLETED"
    assert "payment-a:tranche1" in result["payoutFailures"]
    assert "payment-a:tranche2" in result["payoutFailures"]
    assert not any("payment-b" in f for f in result["payoutFailures"])
