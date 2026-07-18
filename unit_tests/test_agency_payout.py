from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import BadRequestError, PaymentError
from app.models.agency import AgencyTransaction
from app.services.payments import execute_agency_payout


def _fake_payment(**overrides):
    defaults = dict(
        id="payment-1",
        status="CAPTURED",
        trip_amount=500000,
        commission_amount=50000,
        razorpay_payment_id="pay_real_abc123",
        payout_amount_paise=0,
        payout_released=False,
        escrow_status="HELD",
        transfer_status=None,
        payout_frozen=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_agency(**overrides):
    defaults = dict(id="agency-1", owner_id="owner-1")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_bank(**overrides):
    defaults = dict(id="bank-1", agency_id="agency-1", razorpay_account_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_wallet(**overrides):
    defaults = dict(id="wallet-1", agency_id="agency-1", available_balance=0, total_earned=0)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_execute_agency_payout_is_idempotent_once_fully_paid():
    payment = _fake_payment(payout_amount_paise=450000, payout_released=True)  # 500000 - 50000 already paid
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.create_transfer") as mock_transfer:
        result = await execute_agency_payout(db, "payment-1")

    mock_transfer.assert_not_called()
    db.add.assert_not_called()
    assert result["paymentId"] == "payment-1"


@pytest.mark.asyncio
async def test_execute_agency_payout_falls_back_to_manual_without_linked_account():
    payment = _fake_payment()
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id=None)  # not onboarded to Razorpay Route
    wallet = _fake_wallet()

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, bank, wallet])

    with patch("app.services.payments.inv_svc._trip_context", new=AsyncMock(return_value={"agency": agency})), \
         patch("app.services.payments._send_payout_notification", new=AsyncMock()), \
         patch("app.services.payments.create_transfer") as mock_transfer:
        result = await execute_agency_payout(db, "payment-1")

    mock_transfer.assert_not_called()
    assert payment.payout_released is True
    assert payment.payout_amount_paise == 450000  # full agency net (500000 - 50000), one shot
    assert result["transferId"] is None
    assert payment.transfer_status == "MANUAL"

    added = [call.args[0] for call in db.add.call_args_list]
    transactions = [a for a in added if isinstance(a, AgencyTransaction)]
    assert len(transactions) == 1
    assert transactions[0].type == "PAYOUT_MANUAL"
    assert transactions[0].razorpay_transfer_id is None
    assert transactions[0].amount == 450000
    assert wallet.available_balance == transactions[0].amount


@pytest.mark.asyncio
async def test_execute_agency_payout_uses_razorpay_route_when_eligible():
    payment = _fake_payment()
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id="acc_linked123")
    wallet = _fake_wallet()

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, bank, wallet])

    with patch("app.services.payments.inv_svc._trip_context", new=AsyncMock(return_value={"agency": agency})), \
         patch("app.services.payments._send_payout_notification", new=AsyncMock()), \
         patch("app.services.payments.settings") as mock_settings, \
         patch("app.services.payments.create_transfer", return_value={"id": "trf_xyz789"}) as mock_transfer:
        mock_settings.razorpay_key_id = "rzp_test_key"
        mock_settings.razorpay_key_secret = "rzp_test_secret"
        result = await execute_agency_payout(db, "payment-1")

    mock_transfer.assert_called_once()
    called_account_id = mock_transfer.call_args.args[1]
    called_amount = mock_transfer.call_args.args[2]
    assert called_account_id == "acc_linked123"
    assert called_amount == 450000  # full agency net in a single transfer
    assert result["transferId"] == "trf_xyz789"
    assert payment.transfer_status == "SETTLED"
    assert payment.payout_released is True

    added = [call.args[0] for call in db.add.call_args_list]
    transactions = [a for a in added if isinstance(a, AgencyTransaction)]
    assert transactions[0].type == "PAYOUT_ROUTE"
    assert transactions[0].razorpay_transfer_id == "trf_xyz789"


@pytest.mark.asyncio
async def test_execute_agency_payout_tops_up_a_payment_partially_paid_under_the_old_scheme():
    """Migration backfill sets payout_amount_paise to whatever was already
    transferred under the old tranche1/tranche2 split (e.g. 45% sent,
    trip_amount=500000, commission=50000 -> already_paid=202500). This must
    send only the remaining balance, never re-send the whole 90%."""
    payment = _fake_payment(payout_amount_paise=202500)  # 45% of 450000 already sent historically
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id="acc_linked123")
    wallet = _fake_wallet()

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, bank, wallet])

    with patch("app.services.payments.inv_svc._trip_context", new=AsyncMock(return_value={"agency": agency})), \
         patch("app.services.payments._send_payout_notification", new=AsyncMock()), \
         patch("app.services.payments.settings") as mock_settings, \
         patch("app.services.payments.create_transfer", return_value={"id": "trf_topup"}) as mock_transfer:
        mock_settings.razorpay_key_id = "rzp_test_key"
        mock_settings.razorpay_key_secret = "rzp_test_secret"
        result = await execute_agency_payout(db, "payment-1")

    called_amount = mock_transfer.call_args.args[2]
    assert called_amount == 450000 - 202500
    assert result["amount"] == 450000 - 202500
    assert payment.payout_amount_paise == 450000
    assert payment.payout_released is True


@pytest.mark.asyncio
async def test_execute_agency_payout_does_not_flip_released_flag_when_transfer_fails():
    payment = _fake_payment()
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id="acc_linked123")

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, bank])

    with patch("app.services.payments.inv_svc._trip_context", new=AsyncMock(return_value={"agency": agency})), \
         patch("app.services.payments.settings") as mock_settings, \
         patch("app.services.payments.create_transfer", side_effect=PaymentError("linked account not found")):
        mock_settings.razorpay_key_id = "rzp_test_key"
        mock_settings.razorpay_key_secret = "rzp_test_secret"
        with pytest.raises(BadRequestError, match="has not been paid"):
            await execute_agency_payout(db, "payment-1")

    # Money was never confirmed moved — the released flag must stay False.
    assert payment.payout_released is False
    assert payment.payout_amount_paise == 0
    assert payment.transfer_status == "FAILED"


@pytest.mark.asyncio
async def test_execute_agency_payout_skips_route_transfer_for_mock_payments():
    """A mock-captured payment (dev/test checkout, no real Razorpay payment)
    must never attempt a real Route transfer even if the agency has a linked
    account on file."""
    payment = _fake_payment(razorpay_payment_id="pay_mock_abc123")
    agency = _fake_agency()
    bank = _fake_bank(razorpay_account_id="acc_linked123")
    wallet = _fake_wallet()

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, bank, wallet])

    with patch("app.services.payments.inv_svc._trip_context", new=AsyncMock(return_value={"agency": agency})), \
         patch("app.services.payments._send_payout_notification", new=AsyncMock()), \
         patch("app.services.payments.settings") as mock_settings, \
         patch("app.services.payments.create_transfer") as mock_transfer:
        mock_settings.razorpay_key_id = "rzp_test_key"
        mock_settings.razorpay_key_secret = "rzp_test_secret"
        await execute_agency_payout(db, "payment-1")

    mock_transfer.assert_not_called()
