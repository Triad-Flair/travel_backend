"""Two webhook-hardening fixes, both about trusting unauthenticated input:

1. `POST /payments/webhook/razorpay` used to skip signature verification
   entirely whenever the X-Razorpay-Signature header was simply absent —
   `if x_razorpay_signature and not verify_webhook_signature(...)` is only
   ever false-y (no rejection) when the header is missing. Anyone who knew
   an order_id could POST a bare payment.captured body and mark it PAID.
2. handle_razorpay_webhook only recognized "payment.captured", not the
   "order.paid" event Razorpay also fires for the same transition.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services.payments import handle_razorpay_webhook


def _fake_payment(**overrides):
    defaults = dict(id="payment-1", status="PENDING", razorpay_order_id="order_abc", razorpay_payment_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── Router: missing/invalid signature must always be rejected when a secret is configured ──

def _build_test_app():
    from fastapi import FastAPI
    from app.api.v1.payments import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _noop_get_db():
        yield AsyncMock()

    from app.database import get_db
    app.dependency_overrides[get_db] = _noop_get_db
    return app


def test_webhook_rejects_missing_signature_header_when_secret_configured():
    app = _build_test_app()
    client = TestClient(app)

    with patch("app.api.v1.payments.settings") as mock_settings:
        mock_settings.razorpay_webhook_secret = "whsec_configured"
        response = client.post(
            "/api/v1/payments/webhook/razorpay",
            content=b'{"event": "payment.captured", "payload": {"payment": {"entity": {"order_id": "order_abc"}}}}',
        )

    assert response.status_code == 400
    assert "signature" in response.json()["detail"].lower()


def test_webhook_rejects_wrong_signature_when_secret_configured():
    app = _build_test_app()
    client = TestClient(app)

    with patch("app.api.v1.payments.settings") as mock_settings, \
         patch("app.api.v1.payments.verify_webhook_signature", return_value=False):
        mock_settings.razorpay_webhook_secret = "whsec_configured"
        response = client.post(
            "/api/v1/payments/webhook/razorpay",
            content=b"{}",
            headers={"X-Razorpay-Signature": "forged"},
        )

    assert response.status_code == 400


def test_webhook_rejects_everything_when_secret_unconfigured():
    """Fails closed, not open — an unconfigured RAZORPAY_WEBHOOK_SECRET must
    never be treated as 'skip verification'. This endpoint mutates payment
    state with no other auth, so a misconfiguration must block processing,
    not silently trust the request."""
    app = _build_test_app()
    client = TestClient(app)

    with patch("app.api.v1.payments.settings") as mock_settings, \
         patch("app.api.v1.payments.pay_svc.handle_razorpay_webhook", new=AsyncMock()) as mock_handle:
        mock_settings.razorpay_webhook_secret = ""
        response = client.post(
            "/api/v1/payments/webhook/razorpay",
            content=b'{"event": "payment.captured", "payload": {}}',
            headers={"X-Razorpay-Signature": "anything"},
        )

    assert response.status_code == 400
    mock_handle.assert_not_called()


def test_webhook_rejects_malformed_json_body():
    app = _build_test_app()
    client = TestClient(app)

    with patch("app.api.v1.payments.settings") as mock_settings, \
         patch("app.api.v1.payments.verify_webhook_signature", return_value=True):
        mock_settings.razorpay_webhook_secret = "whsec_configured"
        response = client.post(
            "/api/v1/payments/webhook/razorpay",
            content=b"not-json{{{",
            headers={"X-Razorpay-Signature": "valid-per-mock"},
        )

    assert response.status_code == 400


def test_webhook_rejects_oversized_content_length_before_touching_body():
    """A forged huge Content-Length must be rejected before we even read the
    body — cheap DoS defense that doesn't depend on signature verification
    (which itself requires reading the whole body first)."""
    app = _build_test_app()
    client = TestClient(app)

    with patch("app.api.v1.payments.settings") as mock_settings:
        mock_settings.razorpay_webhook_secret = "whsec_configured"
        response = client.post(
            "/api/v1/payments/webhook/razorpay",
            content=b"{}",
            headers={"Content-Length": str(500 * 1024)},
        )

    assert response.status_code == 413


# ── Service: order.paid must be treated as equivalent to payment.captured ──

@pytest.mark.asyncio
async def test_handle_webhook_finalizes_capture_on_order_paid_event():
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        await handle_razorpay_webhook(
            db, "order.paid",
            {"payment": {"entity": {"order_id": "order_abc", "id": "pay_xyz", "amount": None}}},
        )

    mock_finalize.assert_called_once_with(db, payment)
    assert payment.razorpay_payment_id == "pay_xyz"


@pytest.mark.asyncio
async def test_handle_webhook_is_idempotent_across_captured_and_order_paid():
    """Razorpay can fire both events for the same payment — already-CAPTURED
    must not be re-finalized regardless of which event arrives second."""
    payment = _fake_payment(status="CAPTURED")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        await handle_razorpay_webhook(
            db, "order.paid", {"payment": {"entity": {"order_id": "order_abc"}}}
        )

    mock_finalize.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_ignores_unknown_order_id():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    with patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        await handle_razorpay_webhook(
            db, "payment.captured", {"payment": {"entity": {"order_id": "order_does_not_exist"}}}
        )

    mock_finalize.assert_not_called()
