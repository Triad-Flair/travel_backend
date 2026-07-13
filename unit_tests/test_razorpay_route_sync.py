"""Automated Razorpay Route linked-account sync, triggered whenever an
agency's bank details are (re)verified. Must never block bank verification
itself — a Razorpay-side failure, missing profile fields, or Route being
unconfigured should all degrade gracefully rather than raise.

Every test here explicitly mocks create_linked_account/configure_route_
settlement — this suite must never make a real network call to Razorpay
regardless of what's in the local .env (which, at time of writing, holds
real live credentials).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import PaymentError
from app.services.agencies import _sync_razorpay_linked_account


def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1-abcdefghijklmnopqrstuvwxyz", name="Test Travels", email="agency@example.com",
        phone="9999999999", address="123 MG Road", city="Bengaluru", state="Karnataka", postal_code="560001",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_bank(**overrides):
    defaults = dict(
        razorpay_account_id=None, account_number_encrypted="1234567890",
        ifsc_code="HDFC0000001", account_holder_name="Test Travels Pvt Ltd",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_skips_sync_when_razorpay_not_configured():
    agency = _fake_agency()
    bank = _fake_bank()

    with patch("app.services.agencies.settings") as mock_settings:
        mock_settings.razorpay_key_id = ""
        mock_settings.razorpay_key_secret = ""
        with patch("app.services.agencies.create_linked_account") as mock_create:
            account_id, message = await _sync_razorpay_linked_account(agency, bank, "Rahul Sharma")

    mock_create.assert_not_called()
    assert account_id is None
    assert "not configured" in message.lower()


@pytest.mark.asyncio
async def test_skips_sync_when_agency_profile_incomplete():
    agency = _fake_agency(postal_code=None, city=None)
    bank = _fake_bank()

    with patch("app.services.agencies.settings") as mock_settings:
        mock_settings.razorpay_key_id = "rzp_live_x"
        mock_settings.razorpay_key_secret = "secret"
        with patch("app.services.agencies.create_linked_account") as mock_create:
            account_id, message = await _sync_razorpay_linked_account(agency, bank, "Rahul Sharma")

    mock_create.assert_not_called()
    assert account_id is None
    assert "postal_code" in message
    assert "city" in message


@pytest.mark.asyncio
async def test_creates_new_linked_account_when_none_exists():
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id=None)

    with patch("app.services.agencies.settings") as mock_settings:
        mock_settings.razorpay_key_id = "rzp_live_x"
        mock_settings.razorpay_key_secret = "secret"
        with patch(
            "app.services.agencies.create_linked_account", new=AsyncMock(return_value={"id": "acc_new123"})
        ) as mock_create, patch(
            "app.services.agencies.configure_route_settlement", new=AsyncMock()
        ) as mock_configure:
            account_id, message = await _sync_razorpay_linked_account(agency, bank, "Rahul Sharma")

    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["email"] == "agency@example.com"
    # A real person's name (the agency owner), not the bank account holder —
    # Razorpay rejects business-style contact names (confirmed empirically).
    assert call_kwargs["contact_name"] == "Rahul Sharma"
    # Razorpay caps reference_id at 20 chars — our ids are 36-char UUIDs.
    assert call_kwargs["reference_id"] == agency.id[:20]
    assert len(call_kwargs["reference_id"]) <= 20
    mock_configure.assert_called_once_with(
        "acc_new123", account_number="1234567890", ifsc_code="HDFC0000001", beneficiary_name="Test Travels Pvt Ltd",
    )
    assert account_id == "acc_new123"
    assert "Synced" in message


@pytest.mark.asyncio
async def test_reuses_existing_account_does_not_recreate():
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id="acc_existing456")

    with patch("app.services.agencies.settings") as mock_settings:
        mock_settings.razorpay_key_id = "rzp_live_x"
        mock_settings.razorpay_key_secret = "secret"
        with patch("app.services.agencies.create_linked_account") as mock_create, patch(
            "app.services.agencies.configure_route_settlement", new=AsyncMock()
        ) as mock_configure:
            account_id, _ = await _sync_razorpay_linked_account(agency, bank, "Rahul Sharma")

    mock_create.assert_not_called()
    mock_configure.assert_called_once_with(
        "acc_existing456", account_number="1234567890", ifsc_code="HDFC0000001", beneficiary_name="Test Travels Pvt Ltd",
    )
    assert account_id == "acc_existing456"


@pytest.mark.asyncio
async def test_razorpay_failure_does_not_raise_preserves_existing_account_id():
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id="acc_existing456")

    with patch("app.services.agencies.settings") as mock_settings:
        mock_settings.razorpay_key_id = "rzp_live_x"
        mock_settings.razorpay_key_secret = "secret"
        with patch("app.services.agencies.create_linked_account"), patch(
            "app.services.agencies.configure_route_settlement",
            new=AsyncMock(side_effect=PaymentError("Razorpay rejected the settlement details")),
        ):
            account_id, message = await _sync_razorpay_linked_account(agency, bank, "Rahul Sharma")

    # Must not raise — bank verification itself must still succeed.
    assert account_id == "acc_existing456"
    assert "rejected" in message.lower()
