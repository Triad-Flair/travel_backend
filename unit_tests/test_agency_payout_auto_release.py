"""Confirmed live: a real captured payment sat with Transfer: -- on
Razorpay indefinitely — nothing anywhere ever called execute_agency_payout
automatically. It was reachable only via a manual admin endpoint
(POST /payments/agency/payout/{payment_id}) that nobody was hitting.
_release_agency_payout_for_group closes that gap: the agency's full net
share (90% of trip amount) fires the instant every required traveler has
paid and the trip flips to CONFIRMED (see the trip_just_confirmed wiring
in _finalize_capture) — a single transfer per payment, not split across
confirmation and trip completion.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payments import _release_agency_payout_for_group


def _fake_payment(**overrides):
    defaults = dict(id="payment-1", status="CAPTURED", payout_released=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_releases_payout_for_every_eligible_captured_payment():
    payments = [_fake_payment(id="payment-1"), _fake_payment(id="payment-2")]
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = payments
    db.execute = AsyncMock(return_value=result)

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock()) as mock_payout:
        await _release_agency_payout_for_group(db, "group-1", ["user-1", "user-2"])

    assert mock_payout.await_count == 2
    mock_payout.assert_any_await(db, "payment-1")
    mock_payout.assert_any_await(db, "payment-2")


@pytest.mark.asyncio
async def test_one_payout_failure_does_not_block_the_others():
    payments = [_fake_payment(id="payment-1"), _fake_payment(id="payment-2")]
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = payments
    db.execute = AsyncMock(return_value=result)

    async def _side_effect(db, payment_id):
        if payment_id == "payment-1":
            raise RuntimeError("Razorpay Route hiccup")

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock(side_effect=_side_effect)) as mock_payout:
        await _release_agency_payout_for_group(db, "group-1", ["user-1", "user-2"])  # must not raise

    assert mock_payout.await_count == 2


@pytest.mark.asyncio
async def test_no_eligible_payments_is_a_noop():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    with patch("app.services.payments.execute_agency_payout", new=AsyncMock()) as mock_payout:
        await _release_agency_payout_for_group(db, "group-1", ["user-1"])

    mock_payout.assert_not_called()
