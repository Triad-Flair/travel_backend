"""_compute_breakdown is the base/credits/coupon/GST/commission split math —
a pure function, no DB or network, so it's tested here in isolation before
services/payments.py wires it into an actual Razorpay call."""
import pytest

from app.services.payments import (
    FEE_GST_RATE,
    PLATFORM_FEE_PAISE,
    POINTS_USAGE_CAP_RATE,
    WALLET_USAGE_CAP_RATE,
    _compute_breakdown,
)


def test_compute_breakdown_is_pure_and_deterministic():
    result1 = _compute_breakdown(500000)
    result2 = _compute_breakdown(500000)
    assert result1 == result2


def test_compute_breakdown_trip_amount_passthrough():
    breakdown = _compute_breakdown(500000)
    assert breakdown["tripAmount"] == 500000


def test_compute_breakdown_platform_fee_is_fixed():
    breakdown = _compute_breakdown(500000)
    assert breakdown["platformFeeAmount"] == PLATFORM_FEE_PAISE


def test_compute_breakdown_gst_is_18_percent_of_trip_amount():
    """GST is charged on the package price itself, paid by the traveler on
    top — not on the (now-removed) platform fee, and never deducted from
    the agency's payout."""
    breakdown = _compute_breakdown(500000)
    assert breakdown["feeGstAmount"] == int(round(500000 * FEE_GST_RATE))


def test_compute_breakdown_commission_is_10_percent_of_trip_amount():
    breakdown = _compute_breakdown(500000)
    assert breakdown["commissionAmount"] == 50000  # 10% of 500000


def test_compute_breakdown_agency_net_excludes_commission():
    breakdown = _compute_breakdown(500000)
    assert breakdown["agencyNetAmount"] == 500000 - 50000


def test_compute_breakdown_total_is_trip_plus_gst_not_commission():
    """Commission is the platform's cut of the agency's side — it must not
    also be added to what the traveler is charged (total = tripAmount +
    GST only; platformFee is always 0)."""
    breakdown = _compute_breakdown(500000)
    expected_total = 500000 + PLATFORM_FEE_PAISE + int(round(500000 * FEE_GST_RATE))
    assert breakdown["totalAmount"] == expected_total


def test_compute_breakdown_zero_trip_amount():
    breakdown = _compute_breakdown(0)
    assert breakdown["tripAmount"] == 0
    assert breakdown["commissionAmount"] == 0
    assert breakdown["agencyNetAmount"] == 0
    # GST is a percentage of trip amount now, so it's 0 right along with it
    # (no fixed platform fee left to keep GST non-zero at a zero trip amount).
    assert breakdown["totalAmount"] == 0


@pytest.mark.parametrize("trip_amount", [1, 999, 123456, 10_000_000])
def test_compute_breakdown_never_produces_negative_values(trip_amount):
    breakdown = _compute_breakdown(trip_amount)
    assert all(v >= 0 for v in breakdown.values())


def test_wallet_usage_cap_is_50_percent():
    """A traveler can cover up to half the trip amount from wallet balance
    in a single purchase — was 20%, raised per explicit request. Pinned
    here since _get_payment_context (where this is actually applied) has
    too many DB dependencies to unit test directly."""
    assert WALLET_USAGE_CAP_RATE == 0.50


def test_points_usage_cap_is_unchanged_at_20_percent():
    """The wallet cap change was deliberately scoped to wallet only —
    points redemption stays at its existing 20% cap."""
    assert POINTS_USAGE_CAP_RATE == 0.20
