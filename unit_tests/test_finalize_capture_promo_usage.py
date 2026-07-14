"""Confirmed live: _finalize_capture crashed every capture of a
promo-code-carrying payment with asyncpg.exceptions.UndefinedColumnError:
column "updatedAt" of relation "promo_code_usages" does not exist.
PromoCodeUsage was modeled with TimestampsMixin (createdAt/updatedAt), but
the real pre-existing table (from before the FastAPI/SQLAlchemy port) has
no such columns — it tracks discountApplied/usedAt instead, confirmed
against information_schema.columns on the live database. This is a bug
class no AsyncMock-based unit test can catch on its own (flush() is mocked,
so it never actually attempts the real INSERT) — this test instead pins the
PromoCodeUsage(...) construction to use the fields the corrected model
actually declares, so a future edit reverting to the wrong field names
fails immediately instead of only failing against a live database.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.payments import _finalize_capture


def _fake_payment(**overrides):
    defaults = dict(
        id="payment-1", user_id="user-1", group_id="group-1",
        promo_code="WELCOME90", promo_discount_amount=45000,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_promo(**overrides):
    defaults = dict(id="promo-1", code="WELCOME90")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_finalize_capture_records_promo_usage_with_correct_fields():
    payment = _fake_payment()
    promo = _fake_promo()
    db = AsyncMock()
    # Call order inside _finalize_capture: promo lookup, then member lookup,
    # then group lookup (None short-circuits the rest of the function).
    db.scalar = AsyncMock(side_effect=[promo, None, None])
    added = {}
    db.add = lambda obj: added.__setitem__("usage", obj)

    await _finalize_capture(db, payment)

    usage = added["usage"]
    assert usage.promo_id == "promo-1"
    assert usage.user_id == "user-1"
    assert usage.payment_id == "payment-1"
    assert usage.discount_applied == 45000
    assert usage.used_at is not None
    # The bug: these attributes don't exist on the corrected model at all —
    # asserting their absence pins the fix (they'd raise AttributeError on
    # the model this test targets, but on the ORIGINAL TimestampsMixin
    # version they'd silently exist and mask the schema mismatch).
    assert not hasattr(usage, "created_at")
    assert not hasattr(usage, "updated_at")


@pytest.mark.asyncio
async def test_finalize_capture_skips_usage_recording_when_promo_code_absent():
    payment = _fake_payment(promo_code=None)
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[None, None])
    db.add = AsyncMock()

    await _finalize_capture(db, payment)

    db.add.assert_not_called()
