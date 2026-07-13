"""GSTIN/PAN format validation and real GST verification wiring.

Before this, the agency settings page accepted anything typed into the
GSTIN/PAN fields with zero validation (confirmed live — typing "0" into
GSTIN was accepted without complaint) and never called the already-built
verify_gstin() government lookup at all.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import BadRequestError
from app.services.agencies import _assert_gstin_pan_valid


# ── Format validation ───────────────────────────────────────────────────────

def test_rejects_malformed_gstin():
    with pytest.raises(BadRequestError, match="GSTIN format is invalid"):
        _assert_gstin_pan_valid("0", None)


def test_accepts_well_formed_gstin():
    _assert_gstin_pan_valid("29ABCDE1234F1Z5", None)  # must not raise


def test_rejects_malformed_pan():
    with pytest.raises(BadRequestError, match="PAN format is invalid"):
        _assert_gstin_pan_valid(None, "not-a-pan")


def test_accepts_well_formed_pan():
    _assert_gstin_pan_valid(None, "ABCDE1234F")  # must not raise


def test_rejects_pan_that_does_not_match_gstin_embedded_pan():
    # Characters 3-12 of a GSTIN are the PAN of the entity it belongs to.
    with pytest.raises(BadRequestError, match="does not match the PAN embedded"):
        _assert_gstin_pan_valid("29ABCDE1234F1Z5", "ZZZZZ9999Z")


def test_accepts_pan_matching_gstin_embedded_pan():
    _assert_gstin_pan_valid("29ABCDE1234F1Z5", "ABCDE1234F")  # must not raise


def test_allows_neither_field_present():
    _assert_gstin_pan_valid(None, None)  # must not raise


# ── submit_verification wires the real GST government lookup ───────────────

def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1", owner_id="owner-1", slug="test-agency",
        gstin=None, pan=None, name="Test Agency", description=None,
        city=None, state=None, address=None, postal_code=None, phone=None, email=None,
        tourism_license=None, logo_url=None, specializations=None, destinations=None,
        verification_status="pending", verification_rejection_reason=None,
        avg_rating=0.0, review_count=0, total_trips=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_submit_verification_rejects_gstin_that_fails_government_lookup():
    from app.services.agencies import submit_verification

    agency = _fake_agency(gstin=None)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with patch(
        "app.services.agencies.verify_gstin", new=AsyncMock(return_value={"valid": False})
    ):
        with pytest.raises(BadRequestError, match="could not be verified against government records"):
            await submit_verification(db, "agency-1", "owner-1", {"gstin": "29ABCDE1234F1Z5"})


@pytest.mark.asyncio
async def test_submit_verification_accepts_gstin_that_passes_government_lookup():
    from app.services.agencies import submit_verification

    agency = _fake_agency(gstin=None)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with patch(
        "app.services.agencies.verify_gstin",
        new=AsyncMock(return_value={"valid": True, "legal_name": "Test Agency Pvt Ltd"}),
    ) as mock_verify:
        await submit_verification(db, "agency-1", "owner-1", {"gstin": "29ABCDE1234F1Z5"})

    mock_verify.assert_called_once_with("29ABCDE1234F1Z5")
    assert agency.gstin == "29ABCDE1234F1Z5"


@pytest.mark.asyncio
async def test_submit_verification_does_not_hard_block_when_checker_unconfigured():
    """No GSTINCHECK_API_KEY set (true in production today) must degrade to
    accept-for-manual-review, not brick verification submission entirely."""
    from app.services.agencies import submit_verification

    agency = _fake_agency(gstin=None)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with patch(
        "app.services.agencies.verify_gstin",
        new=AsyncMock(return_value={"valid": False, "error": "GST verification not configured"}),
    ):
        await submit_verification(db, "agency-1", "owner-1", {"gstin": "29ABCDE1234F1Z5"})

    assert agency.gstin == "29ABCDE1234F1Z5"


@pytest.mark.asyncio
async def test_submit_verification_skips_gst_lookup_when_gstin_already_set():
    """GSTIN is immutable once set — re-verifying an unchanged value on every
    resubmission would be wasted API calls against an already-accepted GSTIN."""
    from app.services.agencies import submit_verification

    agency = _fake_agency(gstin="29ABCDE1234F1Z5")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with patch("app.services.agencies.verify_gstin", new=AsyncMock()) as mock_verify:
        await submit_verification(db, "agency-1", "owner-1", {"gstin": "29ABCDE1234F1Z5", "description": "Updated"})

    mock_verify.assert_not_called()


@pytest.mark.asyncio
async def test_submit_verification_rejects_malformed_gstin_before_calling_government_lookup():
    from app.services.agencies import submit_verification

    agency = _fake_agency(gstin=None)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with patch("app.services.agencies.verify_gstin", new=AsyncMock()) as mock_verify:
        with pytest.raises(BadRequestError, match="GSTIN format is invalid"):
            await submit_verification(db, "agency-1", "owner-1", {"gstin": "not-a-real-gstin"})

    mock_verify.assert_not_called()
