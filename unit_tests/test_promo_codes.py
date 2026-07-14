"""Promo/coupon codes were previously pure vaporware: PromotionalDiscount had
only `code`/`isActive` (no discount type/value/limits), validate_promo()
always returned "unavailable" without touching the DB, and
create_payment_order() never read req.promo_code at all — a coupon row could
be inserted but nothing in checkout would ever honor it.

These tests cover the real _resolve_promo_discount lookup/eligibility logic
and the admin create_promo_code validation, mirroring the "test the pure
logic in isolation" approach used for _compute_breakdown in
test_payment_split.py — create_payment_order's full flow has too many DB
dependencies (Group/GroupMember/Plan/Offer/Agency/Razorpay) to usefully mock
end-to-end here.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exceptions import BadRequestError
from app.services.payments import _resolve_promo_discount, create_promo_code, validate_promo
from app.schemas.payments import CreatePromoCodeRequest, ValidatePromoRequest


def _fake_promo(**overrides):
    defaults = dict(
        id="promo-1",
        code="WELCOME90",
        is_active=True,
        description=None,
        discount_type="PERCENTAGE",
        discount_value=90,
        max_discount_paise=None,
        min_order_amount_paise=None,
        usage_limit=None,
        per_user_limit=1,
        expires_at=None,
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _db_returning(promo, usage_counts=()):
    """db.scalar is called once for the promo lookup, then once per
    usage-limit check still in effect (usage_limit, then per_user_limit)."""
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[promo, *usage_counts])
    return db


# ── _resolve_promo_discount ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_promo_rejects_unknown_code():
    db = _db_returning(None)
    promo, discount, message = await _resolve_promo_discount(db, "NOPE", "user-1", 500000)
    assert promo is None
    assert discount == 0
    assert message == "Invalid promo code"


@pytest.mark.asyncio
async def test_resolve_promo_rejects_inactive_code():
    db = _db_returning(_fake_promo(is_active=False))
    promo, discount, _ = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500000)
    assert promo is None
    assert discount == 0


@pytest.mark.asyncio
async def test_resolve_promo_rejects_expired_code():
    db = _db_returning(_fake_promo(expires_at=datetime.now(UTC) - timedelta(days=1)))
    promo, discount, message = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500000)
    assert promo is None
    assert discount == 0
    assert "expired" in message


@pytest.mark.asyncio
async def test_resolve_promo_rejects_below_minimum_order_amount():
    db = _db_returning(_fake_promo(min_order_amount_paise=1_000_00))
    promo, discount, message = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500_00)
    assert promo is None
    assert discount == 0
    assert "Minimum order amount" in message


@pytest.mark.asyncio
async def test_resolve_promo_rejects_when_total_usage_limit_reached():
    db = _db_returning(_fake_promo(usage_limit=5), usage_counts=[5])
    promo, discount, message = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500000)
    assert promo is None
    assert discount == 0
    assert "usage limit" in message


@pytest.mark.asyncio
async def test_resolve_promo_rejects_when_per_user_limit_reached():
    db = _db_returning(_fake_promo(per_user_limit=1), usage_counts=[1])
    promo, discount, message = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500000)
    assert promo is None
    assert discount == 0
    assert "already used" in message


@pytest.mark.asyncio
async def test_resolve_promo_computes_percentage_discount():
    db = _db_returning(_fake_promo(discount_type="PERCENTAGE", discount_value=90, per_user_limit=None))
    promo, discount, message = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500_00)
    assert promo is not None
    assert discount == 450_00  # 90% of 500 rupees, in paise
    assert message == "Promo code applied"


@pytest.mark.asyncio
async def test_resolve_promo_percentage_discount_capped_by_max_discount():
    db = _db_returning(
        _fake_promo(discount_type="PERCENTAGE", discount_value=90, max_discount_paise=100_00, per_user_limit=None)
    )
    _, discount, _ = await _resolve_promo_discount(db, "WELCOME90", "user-1", 500_00)
    assert discount == 100_00


@pytest.mark.asyncio
async def test_resolve_promo_flat_discount_never_exceeds_gross_amount():
    db = _db_returning(
        _fake_promo(discount_type="FLAT", discount_value=1000_00, per_user_limit=None)
    )
    _, discount, _ = await _resolve_promo_discount(db, "WELCOME90", "user-1", 300_00)
    assert discount == 300_00


@pytest.mark.asyncio
async def test_resolve_promo_rejects_blank_code():
    db = AsyncMock()
    promo, discount, message = await _resolve_promo_discount(db, "   ", "user-1", 500000)
    assert promo is None
    assert discount == 0
    assert message == "Enter a promo code"
    db.scalar.assert_not_called()


# ── validate_promo (wraps _resolve_promo_discount with payment context) ──

@pytest.mark.asyncio
async def test_validate_promo_returns_valid_response(monkeypatch):
    from app.services import payments as pay_svc

    ctx = {"breakdown": {"totalAmount": 500_00}}
    monkeypatch.setattr(pay_svc, "_get_payment_context", AsyncMock(return_value=ctx))
    db = _db_returning(_fake_promo(discount_type="PERCENTAGE", discount_value=90, per_user_limit=None))

    result = await validate_promo(db, ValidatePromoRequest(code="WELCOME90", group_id="group-1"), "user-1")

    assert result.valid is True
    assert result.discount_paise == 450_00
    assert result.discount_type == "PERCENTAGE"


@pytest.mark.asyncio
async def test_validate_promo_returns_invalid_response_for_bad_code(monkeypatch):
    from app.services import payments as pay_svc

    ctx = {"breakdown": {"totalAmount": 500_00}}
    monkeypatch.setattr(pay_svc, "_get_payment_context", AsyncMock(return_value=ctx))
    db = _db_returning(None)

    result = await validate_promo(db, ValidatePromoRequest(code="NOPE", group_id="group-1"), "user-1")

    assert result.valid is False
    assert result.discount_paise is None


# ── create_promo_code (admin) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_promo_code_rejects_blank_code():
    db = AsyncMock()
    with pytest.raises(BadRequestError, match="required"):
        await create_promo_code(
            db, CreatePromoCodeRequest(code="  ", discount_type="PERCENTAGE", discount_value=90)
        )


@pytest.mark.asyncio
async def test_create_promo_code_rejects_bad_discount_type():
    db = AsyncMock()
    with pytest.raises(BadRequestError, match="PERCENTAGE or FLAT"):
        await create_promo_code(
            db, CreatePromoCodeRequest(code="X", discount_type="WEIRD", discount_value=90)
        )


@pytest.mark.asyncio
async def test_create_promo_code_rejects_zero_discount_value():
    db = AsyncMock()
    with pytest.raises(BadRequestError, match="greater than 0"):
        await create_promo_code(
            db, CreatePromoCodeRequest(code="X", discount_type="PERCENTAGE", discount_value=0)
        )


@pytest.mark.asyncio
async def test_create_promo_code_rejects_percentage_over_100():
    db = AsyncMock()
    with pytest.raises(BadRequestError, match="cannot exceed 100"):
        await create_promo_code(
            db, CreatePromoCodeRequest(code="X", discount_type="PERCENTAGE", discount_value=150)
        )


@pytest.mark.asyncio
async def test_create_promo_code_rejects_duplicate_code():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=_fake_promo(code="WELCOME90"))
    with pytest.raises(BadRequestError, match="already exists"):
        await create_promo_code(
            db, CreatePromoCodeRequest(code="welcome90", discount_type="PERCENTAGE", discount_value=90)
        )


@pytest.mark.asyncio
async def test_create_promo_code_happy_path_uppercases_code():
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[None, 0])  # no existing code, then times_used count
    created = {}
    db.add = lambda obj: created.__setitem__("promo", obj)

    async def _flush():
        # Mimics SQLAlchemy fetching the server_default createdAt via
        # RETURNING on a real flush — a bare AsyncMock() wouldn't populate it.
        created["promo"].created_at = datetime.now(UTC)

    db.flush = AsyncMock(side_effect=_flush)

    result = await create_promo_code(
        db, CreatePromoCodeRequest(code="welcome90", discount_type="PERCENTAGE", discount_value=90)
    )

    assert result.code == "WELCOME90"
    assert result.is_active is True
    assert result.times_used == 0
