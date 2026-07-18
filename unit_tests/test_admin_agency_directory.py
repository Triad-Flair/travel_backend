"""Super Admin Dashboard — agency directory, per-field verification flags,
and the operational status gate (PENDING/APPROVED/REJECTED/PAUSED/
SUSPENDED). Deliberately separate from the existing verification_status
(KYC/GST review) column and its list_pending_verification_agencies queue —
see the migration docstring for why these are additive, not a replacement.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError, NotFoundError
from app.services.agencies import (
    list_all_agencies_admin,
    set_verification_flags,
    update_agency_operational_status,
)


def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1", name="Test Agency", slug="test-agency", email="a@b.com", phone="+911234567890",
        gstin="07AAICS1234A1Z9", pan="AAICS1234A", tourism_license="TL-123",
        status="PENDING",
        name_verified=False, email_verified=False, phone_verified=False,
        bank_details_verified=False, gst_verified=False, pan_verified=False, travel_license_verified=False,
        verification_rejection_reason=None,
        created_at=__import__("datetime").datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_bank(**overrides):
    defaults = dict(
        account_holder_name="Test Agency Pvt Ltd", bank_name="HDFC Bank",
        account_number_encrypted="1234567890", ifsc_code="HDFC0000123",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_list_all_agencies_admin_returns_every_agency_regardless_of_status():
    agencies = [_fake_agency(id="agency-1", status="PENDING"), _fake_agency(id="agency-2", status="APPROVED")]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=agencies)))))
    db.scalar = AsyncMock(return_value=None)  # no bank record for either

    result = await list_all_agencies_admin(db)

    assert [a.id for a in result] == ["agency-1", "agency-2"]
    assert result[0].status == "PENDING"
    assert result[1].status == "APPROVED"
    assert result[0].bank_details is None


@pytest.mark.asyncio
async def test_list_all_agencies_admin_includes_masked_bank_details_when_present():
    agency = _fake_agency()
    bank = _fake_bank()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[agency])))))
    db.scalar = AsyncMock(return_value=bank)

    result = await list_all_agencies_admin(db)

    assert result[0].bank_details is not None
    assert result[0].bank_details.bank_name == "HDFC Bank"
    assert result[0].bank_details.masked_account_number == "******7890"


@pytest.mark.asyncio
async def test_list_all_agencies_admin_reports_all_flags_verified():
    fully_verified = _fake_agency(
        id="agency-1", name_verified=True, email_verified=True, phone_verified=True,
        bank_details_verified=True, gst_verified=True, pan_verified=True, travel_license_verified=True,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[fully_verified])))))
    db.scalar = AsyncMock(return_value=None)

    result = await list_all_agencies_admin(db)

    assert result[0].all_flags_verified is True
    assert result[0].verification_flags.name is True
    assert result[0].verification_flags.bank_details is True


@pytest.mark.asyncio
async def test_set_verification_flags_partially_updates_only_given_keys():
    agency = _fake_agency(gst_verified=False, pan_verified=False)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None])  # agency lookup, then bank lookup for the response

    with patch("app.services.agencies.invalidate", new=AsyncMock()):
        result = await set_verification_flags(db, "agency-1", {"gst": True})

    assert agency.gst_verified is True
    assert agency.pan_verified is False  # untouched
    assert result.verification_flags.gst is True
    assert result.verification_flags.pan is False


@pytest.mark.asyncio
async def test_set_verification_flags_ignores_unknown_keys():
    agency = _fake_agency()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None])

    with patch("app.services.agencies.invalidate", new=AsyncMock()):
        await set_verification_flags(db, "agency-1", {"not_a_real_flag": True, "name": True})

    assert agency.name_verified is True


@pytest.mark.asyncio
async def test_set_verification_flags_raises_when_agency_not_found():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await set_verification_flags(db, "missing-agency", {"name": True})


@pytest.mark.asyncio
async def test_update_agency_status_blocks_approval_when_flags_incomplete():
    agency = _fake_agency(gst_verified=False)  # everything else true except gst
    for f in ("name_verified", "email_verified", "phone_verified", "bank_details_verified", "pan_verified", "travel_license_verified"):
        setattr(agency, f, True)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with pytest.raises(BadRequestError, match="must be verified"):
        await update_agency_operational_status(db, "agency-1", "APPROVED")

    assert agency.status == "PENDING"  # unchanged


@pytest.mark.asyncio
async def test_update_agency_status_approves_when_all_flags_verified():
    agency = _fake_agency()
    for f in ("name_verified", "email_verified", "phone_verified", "bank_details_verified", "gst_verified", "pan_verified", "travel_license_verified"):
        setattr(agency, f, True)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None])

    with patch("app.services.agencies.invalidate", new=AsyncMock()):
        result = await update_agency_operational_status(db, "agency-1", "APPROVED")

    assert agency.status == "APPROVED"
    assert result.status == "APPROVED"


@pytest.mark.asyncio
async def test_update_agency_status_allows_pause_and_suspend_without_flags():
    agency = _fake_agency(status="APPROVED")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None, agency, None])

    with patch("app.services.agencies.invalidate", new=AsyncMock()):
        await update_agency_operational_status(db, "agency-1", "PAUSED")
        assert agency.status == "PAUSED"

        await update_agency_operational_status(db, "agency-1", "SUSPENDED")
        assert agency.status == "SUSPENDED"


@pytest.mark.asyncio
async def test_update_agency_status_records_rejection_reason():
    agency = _fake_agency()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None])

    with patch("app.services.agencies.invalidate", new=AsyncMock()):
        await update_agency_operational_status(db, "agency-1", "REJECTED", "Documents unclear")

    assert agency.status == "REJECTED"
    assert agency.verification_rejection_reason == "Documents unclear"


@pytest.mark.asyncio
async def test_update_agency_status_clears_rejection_reason_on_approval():
    agency = _fake_agency(verification_rejection_reason="old reason")
    for f in ("name_verified", "email_verified", "phone_verified", "bank_details_verified", "gst_verified", "pan_verified", "travel_license_verified"):
        setattr(agency, f, True)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None])

    with patch("app.services.agencies.invalidate", new=AsyncMock()):
        await update_agency_operational_status(db, "agency-1", "APPROVED")

    assert agency.verification_rejection_reason is None
