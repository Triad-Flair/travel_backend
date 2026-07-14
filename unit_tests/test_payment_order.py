"""Same bug class caught live in production three times already (packages.py
publish_package/update_package, plans.py update_plan): create_payment_order
mutates/creates a Payment row, flushes, then _payment_to_response reads
payment.updated_at (onupdate=func.now(), expired by the flush) — a bare
synchronous re-read of an expired attribute crashes with
sqlalchemy.exc.MissingGreenlet outside an async context. Confirmed live on
POST /payments/groups/{id}/order (the exact request that included a
promoCode) — create_payment_order never got the db.refresh(payment) call
that verify_payment/mock_capture already have.
"""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.payments import CreateOrderRequest
from app.services.payments import _compute_breakdown, create_payment_order


def _fake_agency(**overrides):
    defaults = dict(id="agency-1", name="Test Agency")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_package(**overrides):
    defaults = dict(id="pkg-1", title="Goa Package")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_create_payment_order_refreshes_new_payment_after_flush(monkeypatch):
    from app.services import payments as pay_svc

    breakdown = _compute_breakdown(500_00)
    ctx = {
        "payment": None,
        "breakdown": breakdown,
        "plan": None,
        "package": _fake_package(),
        "agency": _fake_agency(),
        "payment_source": "PACKAGE",
        "max_redeemable_points": 0,
        "max_wallet_usable_rupees": 0,
    }
    monkeypatch.setattr(pay_svc, "_get_payment_context", AsyncMock(return_value=ctx))
    # Force the mock checkout path deterministically — this test isolates
    # the db.refresh regression, not whichever Razorpay credentials happen
    # to be configured in the local/CI environment.
    monkeypatch.setattr(pay_svc.settings, "razorpay_key_id", "")
    monkeypatch.setattr(pay_svc.settings, "razorpay_key_secret", "")

    db = AsyncMock()
    added = {}
    db.add = lambda obj: added.__setitem__("payment", obj)

    async def _refresh_side_effect(obj):
        # Mimics a real flush/refresh populating the server-generated
        # timestamp columns — a bare AsyncMock() wouldn't.
        obj.created_at = datetime.now(UTC)
        obj.updated_at = datetime.now(UTC)

    db.refresh = AsyncMock(side_effect=_refresh_side_effect)
    db.flush = AsyncMock()

    result = await create_payment_order(
        db, "group-1", "user-1", CreateOrderRequest(points_to_redeem=0, wallet_amount_to_use=0)
    )

    db.refresh.assert_awaited_once_with(added["payment"])
    assert result.checkout_mode == "mock"
    assert result.payment.status == "PENDING"
