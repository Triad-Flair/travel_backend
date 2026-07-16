"""Confirmed live: the Refer & Earn dashboard showed "1 completed, ₹250
earned" right after a real referral, then reset to "0 completed, ₹0
earned" the next time the referrer reloaded that same page. get_my_referrals
read from ReferralLink.used_by_user_id, a single nullable column that
get_or_create_referral_link resets to None on every dashboard load (to
rotate in a fresh shareable code once the old one is used) — so it could
never hold more than one referral's history, and lost even that one on the
very next page view. Rewritten to read from ReferralWalletTransaction (one
durable row per completed referral, populated by credit_referral_bonus),
which has no such one-slot limit and isn't touched by the link-rotation
logic at all.
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.loyalty import REFERRAL_BONUS_RUPEES, credit_referral_bonus, get_my_referrals


def _fake_wallet(**overrides):
    defaults = dict(id="wallet-1", user_id="referrer-1", balance=0, total_earned=0, total_spent=0)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_transaction(**overrides):
    defaults = dict(
        id="tx-1", wallet_id="wallet-1", type="REFERRAL_BONUS", amount=REFERRAL_BONUS_RUPEES,
        reference_id="referred-1", created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_user(**overrides):
    defaults = dict(
        id="referred-1", display_name="Sanjay Bisht", username="sanjaybisht",
        email="sanjaybishtsb90@gmail.com", avatar_url=None, created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── credit_referral_bonus ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_credit_referral_bonus_creates_wallet_when_none_exists():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    added = []
    db.add = lambda obj: added.append(obj)

    await credit_referral_bonus(db, "referrer-1", "referred-1", referral_link_id="link-1")

    wallet = next(obj for obj in added if type(obj).__name__ == "ReferralWallet")
    transaction = next(obj for obj in added if type(obj).__name__ == "ReferralWalletTransaction")
    assert wallet.balance == REFERRAL_BONUS_RUPEES
    assert wallet.total_earned == REFERRAL_BONUS_RUPEES
    # idempotencyKey is NOT NULL with a real unique constraint on the live
    # table — a second credit attempt for the same referred user must fail
    # at the DB level rather than silently double-crediting.
    assert transaction.idempotency_key == "referral:referred-1"
    assert transaction.referral_id == "link-1"
    assert transaction.type == "referral_earned"


@pytest.mark.asyncio
async def test_credit_referral_bonus_adds_to_existing_balance():
    wallet = _fake_wallet(balance=500, total_earned=500)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=wallet)

    await credit_referral_bonus(db, "referrer-1", "referred-1")

    assert wallet.balance == 500 + REFERRAL_BONUS_RUPEES
    assert wallet.total_earned == 500 + REFERRAL_BONUS_RUPEES


# ── get_my_referrals reads the durable ledger, not the single-slot link ──

@pytest.mark.asyncio
async def test_get_my_referrals_returns_empty_when_no_wallet_exists():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    result = await get_my_referrals(db, "referrer-1", 1, 20)

    assert result["referrals"] == []
    assert result["pagination"]["total"] == 0


@pytest.mark.asyncio
async def test_get_my_referrals_lists_completed_referrals_from_wallet_transactions():
    wallet = _fake_wallet()
    tx = _fake_transaction()
    referred_user = _fake_user()

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[wallet, 1, referred_user])  # wallet, total count, referred user lookup
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [tx]
    db.execute = AsyncMock(return_value=execute_result)

    result = await get_my_referrals(db, "referrer-1", 1, 20)

    assert result["pagination"]["total"] == 1
    referral = result["referrals"][0]
    assert referral["status"] == "COMPLETED"
    assert referral["earnedAmount"] == REFERRAL_BONUS_RUPEES
    assert referral["referredUser"]["fullName"] == "Sanjay Bisht"


@pytest.mark.asyncio
async def test_get_my_referrals_survives_a_deleted_or_missing_referred_user():
    wallet = _fake_wallet()
    tx = _fake_transaction(reference_id="ghost-user")

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[wallet, 1, None])  # referred user lookup returns None
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [tx]
    db.execute = AsyncMock(return_value=execute_result)

    result = await get_my_referrals(db, "referrer-1", 1, 20)

    assert result["referrals"][0]["referredUser"]["id"] == "ghost-user"
    assert result["referrals"][0]["referredUser"]["fullName"] == "New user"
