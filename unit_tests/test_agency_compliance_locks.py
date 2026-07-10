"""Agency settings compliance rules:
  1. GSTIN and PAN are immutable once set (update_agency + submit_verification).
  2. Verified bank details are locked — resubmission requires an explicit
     confirmChange flag, matching the settings page's "Change bank details" flow.
  3. IFSC lookup maps the external response into IfscLookupResponse.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError


def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1", owner_id="owner-1", slug="test-agency",
        gstin=None, pan=None, name="Test Agency", description=None,
        city=None, state=None, address=None, phone=None, email=None,
        tourism_license=None, logo_url=None, specializations=None, destinations=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── Rule 1: GSTIN/PAN immutable once set ────────────────────────────────────

def test_assert_gstin_pan_immutable_rejects_gstin_change():
    from app.services.agencies import _assert_gstin_pan_immutable

    agency = _fake_agency(gstin="07AAICS1234A1Z9")
    with pytest.raises(BadRequestError, match="GSTIN cannot be changed"):
        _assert_gstin_pan_immutable(agency, "29DIFFERENT123A1Z1", None)


def test_assert_gstin_pan_immutable_rejects_pan_change():
    from app.services.agencies import _assert_gstin_pan_immutable

    agency = _fake_agency(pan="AAICS1234A")
    with pytest.raises(BadRequestError, match="PAN cannot be changed"):
        _assert_gstin_pan_immutable(agency, None, "ZZZZZ9999Z")


def test_assert_gstin_pan_immutable_allows_resubmitting_same_value():
    from app.services.agencies import _assert_gstin_pan_immutable

    agency = _fake_agency(gstin="07AAICS1234A1Z9", pan="AAICS1234A")
    _assert_gstin_pan_immutable(agency, "07AAICS1234A1Z9", "AAICS1234A")  # should not raise


def test_assert_gstin_pan_immutable_allows_first_time_set():
    from app.services.agencies import _assert_gstin_pan_immutable

    agency = _fake_agency()  # gstin/pan both None
    _assert_gstin_pan_immutable(agency, "07AAICS1234A1Z9", "AAICS1234A")  # should not raise


@pytest.mark.asyncio
async def test_update_agency_rejects_gstin_change():
    from app.schemas.agencies import UpdateAgencyRequest
    from app.services.agencies import update_agency

    agency = _fake_agency(gstin="07AAICS1234A1Z9")
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = agency
    db.execute = AsyncMock(return_value=exec_result)

    with pytest.raises(BadRequestError, match="GSTIN cannot be changed"):
        await update_agency(db, "agency-1", "owner-1", UpdateAgencyRequest(gstin="29DIFFERENT123A1Z1"))


@pytest.mark.asyncio
async def test_submit_verification_rejects_pan_change():
    from app.services.agencies import submit_verification

    agency = _fake_agency(pan="AAICS1234A")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with pytest.raises(BadRequestError, match="PAN cannot be changed"):
        await submit_verification(db, "agency-1", "owner-1", {"pan": "ZZZZZ9999Z"})


# ── Rule 2: verified bank details locked without confirmChange ─────────────

def _fake_bank(**overrides):
    defaults = dict(
        id="bank-1", agency_id="agency-1", account_number_encrypted="1234567890",
        ifsc_code="HDFC0000001", account_holder_name="Test Agency", bank_name="HDFC Bank",
        branch_name="Nariman Point", razorpay_account_id=None, verification_status="VERIFIED", updated_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_verify_bank_account_rejects_resubmission_without_confirm_change():
    from app.services.agencies import verify_bank_account

    agency = _fake_agency()
    bank = _fake_bank(verification_status="VERIFIED")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, bank])

    with pytest.raises(BadRequestError, match="already verified and locked"):
        await verify_bank_account(db, "agency-1", "owner-1", {
            "accountNumber": "9999999999", "ifscCode": "ICIC0000001", "accountHolderName": "Test Agency",
        })


@pytest.mark.asyncio
async def test_verify_bank_account_allows_resubmission_with_confirm_change():
    from app.services.agencies import verify_bank_account

    agency = _fake_agency()
    bank = _fake_bank(verification_status="VERIFIED")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, bank])

    result = await verify_bank_account(db, "agency-1", "owner-1", {
        "accountNumber": "9999999999", "ifscCode": "ICIC0000001", "accountHolderName": "Test Agency",
        "branchName": "MG Road", "confirmChange": True,
    })

    assert result["verificationStatus"] == "VERIFIED"
    assert bank.ifsc_code == "ICIC0000001"
    assert bank.branch_name == "MG Road"


@pytest.mark.asyncio
async def test_verify_bank_account_persists_branch_name_on_first_submission():
    from app.services.agencies import verify_bank_account

    agency = _fake_agency()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[agency, None])  # no existing bank record

    await verify_bank_account(db, "agency-1", "owner-1", {
        "accountNumber": "1234567890", "ifscCode": "HDFC0000001", "accountHolderName": "Test Agency",
        "bankName": "HDFC Bank", "branchName": "Nariman Point",
    })

    added = [call.args[0] for call in db.add.call_args_list]
    assert added[0].branch_name == "Nariman Point"


# ── Rule 3: IFSC lookup mapping ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lookup_ifsc_maps_valid_response():
    from app.services.agencies import lookup_ifsc

    with patch(
        "app.services.agencies.lookup_ifsc_code",
        new=AsyncMock(return_value={
            "valid": True, "bank": "HDFC Bank", "branch": "Nariman Point",
            "address": "101 Tulsiani Chambers", "city": "Mumbai", "state": "Maharashtra", "district": "Mumbai",
        }),
    ):
        result = await lookup_ifsc("HDFC0000001")

    assert result.valid is True
    assert result.bank == "HDFC Bank"
    assert result.branch == "Nariman Point"


@pytest.mark.asyncio
async def test_lookup_ifsc_maps_invalid_response():
    from app.services.agencies import lookup_ifsc

    with patch("app.services.agencies.lookup_ifsc_code", new=AsyncMock(return_value={"valid": False})):
        result = await lookup_ifsc("NOTREAL0001")

    assert result.valid is False
    assert result.bank is None
