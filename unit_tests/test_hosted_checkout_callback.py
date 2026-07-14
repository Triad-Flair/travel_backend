"""handle_hosted_checkout_callback backs the full-page redirect checkout
flow (Razorpay's hosted payment page form-POSTs the browser back here with
no auth session attached — the HMAC signature is the only trust boundary).
It must never raise: there's no request originator left to hand an error
response to, only a redirect target to pick.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.payments import handle_hosted_checkout_callback


def _build_test_app():
    from fastapi import FastAPI
    from app.api.v1.payments import router
    from app.database import get_db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _noop_get_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _noop_get_db
    return app


def _fake_payment(**overrides):
    defaults = dict(
        id="payment-1", group_id="group-1", status="PENDING",
        razorpay_order_id="order_abc", razorpay_payment_id=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_missing_fields_redirects_to_generic_failure_without_db_lookup():
    db = AsyncMock()

    url = await handle_hosted_checkout_callback(db, None, "pay_x", "sig_x")

    assert "payment=failed" in url
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_order_id_redirects_to_generic_failure():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    url = await handle_hosted_checkout_callback(db, "order_ghost", "pay_x", "sig_x")

    assert "payment=failed" in url
    assert "dashboard/trips" in url


@pytest.mark.asyncio
async def test_already_captured_is_idempotent_no_reverification():
    payment = _fake_payment(status="CAPTURED")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.verify_signature") as mock_verify, \
         patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        url = await handle_hosted_checkout_callback(db, "order_abc", "pay_x", "sig_x")

    mock_verify.assert_not_called()
    mock_finalize.assert_not_called()
    assert "payment=success" in url
    assert "group-1" in url


@pytest.mark.asyncio
async def test_invalid_signature_redirects_to_failure_without_capturing():
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.verify_signature", return_value=False), \
         patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        url = await handle_hosted_checkout_callback(db, "order_abc", "pay_forged", "sig_bad")

    mock_finalize.assert_not_called()
    assert payment.status == "PENDING"
    assert "payment=failed" in url
    assert "group-1" in url


@pytest.mark.asyncio
async def test_valid_signature_finalizes_capture_and_redirects_to_success():
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.verify_signature", return_value=True), \
         patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        url = await handle_hosted_checkout_callback(db, "order_abc", "pay_real", "sig_good")

    mock_finalize.assert_called_once_with(db, payment)
    assert payment.razorpay_payment_id == "pay_real"
    assert "payment=success" in url
    assert "group-1" in url


@pytest.mark.asyncio
async def test_finalize_capture_crash_still_redirects_to_success_and_rolls_back():
    """Confirmed live: _finalize_capture crashing here (the promo_code_usages
    schema mismatch) propagated all the way to a raw 500 JSON page shown
    directly in the payer's browser — there's no request originator left to
    show an error to on this redirect flow. The signature is already
    verified (Razorpay confirms the money moved), so this must still say
    success and roll back cleanly rather than leaving the session in a
    failed-flush state for get_db()'s commit."""
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)
    db.rollback = AsyncMock()

    with patch("app.services.payments.verify_signature", return_value=True), \
         patch("app.services.payments._finalize_capture", new=AsyncMock(side_effect=RuntimeError("boom"))):
        url = await handle_hosted_checkout_callback(db, "order_abc", "pay_real", "sig_good")

    db.rollback.assert_awaited_once()
    assert "payment=success" in url
    assert "group-1" in url


# ── Router: parses the form POST and issues a 303 (never re-POSTs the body) ─

def test_router_parses_form_post_and_redirects_with_303():
    app = _build_test_app()
    client = TestClient(app, follow_redirects=False)

    with patch("app.api.v1.payments.pay_svc.handle_hosted_checkout_callback", new=AsyncMock(return_value="https://travellersin.com/x?payment=success")) as mock_handle:
        response = client.post(
            "/api/v1/payments/razorpay-callback",
            data={
                "razorpay_order_id": "order_abc",
                "razorpay_payment_id": "pay_real",
                "razorpay_signature": "sig_good",
            },
        )

    assert response.status_code == 303
    assert response.headers["location"] == "https://travellersin.com/x?payment=success"
    mock_handle.assert_called_once()
    call_args = mock_handle.call_args.args
    assert call_args[1:] == ("order_abc", "pay_real", "sig_good")


def test_router_handles_missing_form_fields_without_500():
    app = _build_test_app()
    client = TestClient(app, follow_redirects=False)

    with patch("app.api.v1.payments.pay_svc.handle_hosted_checkout_callback", new=AsyncMock(return_value="https://travellersin.com/dashboard/trips?payment=failed")):
        response = client.post("/api/v1/payments/razorpay-callback", data={})

    assert response.status_code == 303
