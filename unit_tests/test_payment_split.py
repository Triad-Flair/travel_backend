"""_compute_breakdown is the base/credits/coupon/GST/commission split math —
a pure function, no DB or network, so it's tested here in isolation before
services/payments.py wires it into an actual Razorpay call."""
import pytest

from app.services.payments import (
    FEE_GST_RATE,
    PLATFORM_FEE_PAISE,
    TRANCHE_1_RATIO,
    TRANCHE_2_RATIO,
    _compute_breakdown,
    _tranche_amount,
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


def test_compute_breakdown_gst_is_18_percent_of_platform_fee():
    breakdown = _compute_breakdown(500000)
    assert breakdown["feeGstAmount"] == int(round(PLATFORM_FEE_PAISE * FEE_GST_RATE))


def test_compute_breakdown_commission_is_10_percent_of_trip_amount():
    breakdown = _compute_breakdown(500000)
    assert breakdown["commissionAmount"] == 50000  # 10% of 500000


def test_compute_breakdown_agency_net_excludes_commission():
    breakdown = _compute_breakdown(500000)
    assert breakdown["agencyNetAmount"] == 500000 - 50000


def test_compute_breakdown_total_is_trip_plus_fee_plus_gst_not_commission():
    """Commission is the platform's cut of the agency's side — it must not
    also be added to what the traveler is charged (total = tripAmount +
    platformFee + GST only)."""
    breakdown = _compute_breakdown(500000)
    expected_total = 500000 + PLATFORM_FEE_PAISE + int(round(PLATFORM_FEE_PAISE * FEE_GST_RATE))
    assert breakdown["totalAmount"] == expected_total


def test_compute_breakdown_zero_trip_amount():
    breakdown = _compute_breakdown(0)
    assert breakdown["tripAmount"] == 0
    assert breakdown["commissionAmount"] == 0
    assert breakdown["agencyNetAmount"] == 0
    # Platform fee + GST still apply even at zero trip amount
    assert breakdown["totalAmount"] == PLATFORM_FEE_PAISE + int(round(PLATFORM_FEE_PAISE * FEE_GST_RATE))


@pytest.mark.parametrize("trip_amount", [1, 999, 123456, 10_000_000])
def test_compute_breakdown_never_produces_negative_values(trip_amount):
    breakdown = _compute_breakdown(trip_amount)
    assert all(v >= 0 for v in breakdown.values())


def test_tranche_amounts_sum_to_agency_net():
    agency_net = 450000
    total = _tranche_amount(agency_net, "tranche1") + _tranche_amount(agency_net, "tranche2")
    # Rounding on each half independently can be off by a paisa or two —
    # assert it's negligibly close, not necessarily bit-exact.
    assert abs(total - agency_net) <= 1


def test_tranche1_is_45_percent():
    assert _tranche_amount(100000, "tranche1") == int(round(100000 * TRANCHE_1_RATIO))


def test_tranche2_is_55_percent():
    assert _tranche_amount(100000, "tranche2") == int(round(100000 * TRANCHE_2_RATIO))


def test_unknown_tranche_label_defaults_to_tranche1_ratio():
    """_tranche_amount treats anything other than the literal string
    'tranche2' as tranche1 — documenting that behavior explicitly."""
    assert _tranche_amount(100000, "garbage") == _tranche_amount(100000, "tranche1")
