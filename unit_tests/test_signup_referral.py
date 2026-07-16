"""Confirmed live: POST /auth/signup/traveler with a referral code returned
a raw 409 "A record with the same unique value already exists" for any
signup that included a valid referral code — signup_traveler wrote the
code the new user typed in (someone else's existing, already-claimed
code) directly onto the NEW user's own `referral_code` column, which is
actually meant to hold that user's own future shareable code and carries
a real unique constraint (referral_links.code / users.referralCode) —
see loyalty.py::_generate_unique_referral_code checking both. Any valid
referral code collided with the referrer's own row on every signup.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.auth import TravelerSignupRequest
from app.services.auth import _redeem_referral_code, signup_traveler


def _fake_link(**overrides):
    defaults = dict(
        id="link-1", user_id="referrer-1", code="ABCD1234",
        expires_at=datetime.now(UTC) + timedelta(days=10),
        used_at=None, used_by_user_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _signup_request(**overrides):
    defaults = dict(
        full_name="Aryan Sharma", username="aryansharma", email="a@example.com",
        phone="9354249191", password="password123", referral_code="ABCD1234",
    )
    defaults.update(overrides)
    return TravelerSignupRequest(**defaults)


# ── _redeem_referral_code ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redeem_referral_code_marks_link_used():
    link = _fake_link()
    db = AsyncMock()
    # 1st scalar: the ReferralLink lookup. 2nd: credit_referral_bonus's own
    # ReferralWallet lookup (None -> it creates a fresh one).
    db.scalar = AsyncMock(side_effect=[link, None])

    await _redeem_referral_code(db, "abcd1234", "new-user-1")

    assert link.used_by_user_id == "new-user-1"
    assert link.used_at is not None


@pytest.mark.asyncio
async def test_redeem_referral_code_credits_referrer_wallet():
    from app.services.loyalty import REFERRAL_BONUS_RUPEES

    link = _fake_link(user_id="referrer-1")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[link, None])
    added = []
    db.add = lambda obj: added.append(obj)

    await _redeem_referral_code(db, "abcd1234", "new-user-1")

    wallet = next(obj for obj in added if type(obj).__name__ == "ReferralWallet")
    transaction = next(obj for obj in added if type(obj).__name__ == "ReferralWalletTransaction")
    assert wallet.user_id == "referrer-1"
    assert wallet.balance == REFERRAL_BONUS_RUPEES
    assert wallet.total_earned == REFERRAL_BONUS_RUPEES
    assert transaction.amount == REFERRAL_BONUS_RUPEES
    assert transaction.reference_id == "new-user-1"
    assert transaction.idempotency_key == "referral:new-user-1"
    assert transaction.referral_id == link.id


@pytest.mark.asyncio
async def test_redeem_referral_code_adds_to_existing_wallet_balance():
    from app.services.loyalty import REFERRAL_BONUS_RUPEES

    link = _fake_link(user_id="referrer-1")
    existing_wallet = SimpleNamespace(id="wallet-1", user_id="referrer-1", balance=100, total_earned=100, total_spent=0)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[link, existing_wallet])

    await _redeem_referral_code(db, "abcd1234", "new-user-1")

    assert existing_wallet.balance == 100 + REFERRAL_BONUS_RUPEES
    assert existing_wallet.total_earned == 100 + REFERRAL_BONUS_RUPEES


@pytest.mark.asyncio
async def test_redeem_referral_code_normalizes_case_and_punctuation():
    link = _fake_link(code="ABCD1234")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[link, None])

    await _redeem_referral_code(db, "abcd-1234", "new-user-1")

    assert link.used_by_user_id == "new-user-1"


@pytest.mark.asyncio
async def test_redeem_referral_code_noop_for_unknown_code():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    await _redeem_referral_code(db, "GHOST999", "new-user-1")  # must not raise


@pytest.mark.asyncio
async def test_redeem_referral_code_noop_when_already_used():
    link = _fake_link(used_by_user_id="someone-else")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=link)

    await _redeem_referral_code(db, "ABCD1234", "new-user-1")

    assert link.used_by_user_id == "someone-else"


@pytest.mark.asyncio
async def test_redeem_referral_code_noop_when_expired():
    link = _fake_link(expires_at=datetime.now(UTC) - timedelta(days=1))
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=link)

    await _redeem_referral_code(db, "ABCD1234", "new-user-1")

    assert link.used_by_user_id is None


@pytest.mark.asyncio
async def test_redeem_referral_code_blank_input_short_circuits_without_query():
    db = AsyncMock()

    await _redeem_referral_code(db, "   ", "new-user-1")

    db.scalar.assert_not_called()


# ── signup_traveler never writes the entered code onto the new user itself ─

@pytest.mark.asyncio
async def test_signup_traveler_does_not_set_own_referral_code_from_input(monkeypatch):
    from app.services import auth as auth_svc

    captured = {}
    db = AsyncMock()
    # Uniqueness pre-checks (phone/username/email) all return None (no clash).
    db.scalar = AsyncMock(return_value=None)
    db.add = lambda obj: captured.__setitem__("user", obj)
    db.flush = AsyncMock()

    monkeypatch.setattr(auth_svc, "_redeem_referral_code", AsyncMock())
    monkeypatch.setattr(auth_svc, "_issue_email_verification_token", lambda user: "token123")

    with patch("app.workers.tasks.send_registration_email_task.delay"):
        await signup_traveler(db, _signup_request(referral_code="SOMEONE-ELSES-CODE"))

    user = captured["user"]
    assert user.referral_code is None
    auth_svc._redeem_referral_code.assert_awaited_once_with(db, "SOMEONE-ELSES-CODE", user.id)


@pytest.mark.asyncio
async def test_signup_traveler_skips_redeem_when_no_referral_code(monkeypatch):
    from app.services import auth as auth_svc

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = AsyncMock()
    db.flush = AsyncMock()

    monkeypatch.setattr(auth_svc, "_redeem_referral_code", AsyncMock())
    monkeypatch.setattr(auth_svc, "_issue_email_verification_token", lambda user: "token123")

    with patch("app.workers.tasks.send_registration_email_task.delay"):
        await signup_traveler(db, _signup_request(referral_code=None))

    auth_svc._redeem_referral_code.assert_not_called()
