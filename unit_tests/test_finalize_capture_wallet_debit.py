"""Confirmed live: payment.wallet_amount_used was recorded on the payment/
invoice at checkout — the traveler's invoice claimed a wallet discount was
applied — but nothing in the capture flow ever actually subtracted it from
ReferralWallet.balance. A traveler's ₹250 balance stayed at ₹250 (and
total_spent at ₹0) after using ₹22 of it at checkout, meaning the same
"spent" credit was reusable indefinitely. _finalize_capture now debits the
wallet and records a checkout_spent ReferralWalletTransaction, idempotent
per payment id via the same real unique constraint used for referral
crediting.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.loyalty import ReferralWalletTransaction
from app.services.payments import _finalize_capture


def _fake_payment(**overrides):
    defaults = dict(
        id="payment-1", user_id="user-1", group_id="group-1",
        promo_code=None, promo_discount_amount=0, wallet_amount_used=22,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_wallet(**overrides):
    defaults = dict(id="wallet-1", user_id="user-1", balance=250, total_spent=0)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_debits_wallet_balance_and_records_transaction():
    payment = _fake_payment(wallet_amount_used=22)
    wallet = _fake_wallet(balance=250, total_spent=0)
    db = AsyncMock()
    # Call order: wallet lookup, idempotency check (no existing tx), then
    # member lookup, then group lookup (None short-circuits the rest).
    db.scalar = AsyncMock(side_effect=[wallet, None, None, None])
    added = []
    db.add = lambda obj: added.append(obj)

    await _finalize_capture(db, payment)

    assert wallet.balance == 250 - 22
    assert wallet.total_spent == 22
    transactions = [a for a in added if isinstance(a, ReferralWalletTransaction)]
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx.type == "checkout_spent"
    assert tx.amount == -22
    assert tx.idempotency_key == "checkout:payment-1"
    assert tx.payment_id == "payment-1"


@pytest.mark.asyncio
async def test_skips_debit_when_wallet_amount_used_is_zero():
    payment = _fake_payment(wallet_amount_used=0)
    db = AsyncMock()
    # No wallet lookup at all — falls straight through to member/group lookups.
    db.scalar = AsyncMock(side_effect=[None, None])
    db.add = AsyncMock()

    await _finalize_capture(db, payment)

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_skips_debit_when_no_wallet_row_exists():
    payment = _fake_payment(wallet_amount_used=22)
    db = AsyncMock()
    # wallet lookup returns None -> no idempotency check attempted.
    db.scalar = AsyncMock(side_effect=[None, None, None])
    db.add = AsyncMock()

    await _finalize_capture(db, payment)

    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_is_idempotent_when_already_debited_for_this_payment():
    payment = _fake_payment(wallet_amount_used=22)
    wallet = _fake_wallet(balance=228, total_spent=22)
    existing_tx = SimpleNamespace(id="tx-1", idempotency_key="checkout:payment-1")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[wallet, existing_tx, None, None])
    db.add = AsyncMock()

    await _finalize_capture(db, payment)

    # Balance/total_spent must not move a second time, and no duplicate
    # transaction gets added.
    assert wallet.balance == 228
    assert wallet.total_spent == 22
    db.add.assert_not_called()
