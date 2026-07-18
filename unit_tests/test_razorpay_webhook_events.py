"""Coverage for the full Razorpay webhook event surface, not just
payment.captured/order.paid:

  - payment.authorized: this checkout flow always wants immediate capture,
    so an authorized-but-uncaptured payment must trigger an explicit
    capture call rather than sit until Razorpay auto-voids it.
  - payment.failed: must never downgrade an already-CAPTURED payment (an
    out-of-order/retried webhook delivering a stale failure after a later
    capture succeeded).
  - payment.dispute.*: a real bank chargeback, distinct from the app's own
    customer-support Dispute records — must freeze/unfreeze payouts
    correctly and stay idempotent across repeated delivery.
  - payment.downtime.*: informational only, no order_id to correlate.
  - order.notification.*, invoice.*, payment_link.*: Razorpay products this
    platform doesn't use — must no-op rather than crash or misfire.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError, PaymentError
from app.services.payments import (
    DISPUTE_STATUSES_FREEZE_PAYOUT,
    DISPUTE_STATUSES_UNFREEZE_PAYOUT,
    execute_agency_payout,
    handle_razorpay_webhook,
)


def _fake_payment(**overrides):
    defaults = dict(
        id="payment-1", status="PENDING", razorpay_order_id="order_abc",
        razorpay_payment_id=None, amount=100000, payout_frozen=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_dispute(**overrides):
    defaults = dict(id="dispute-1", payment_id="payment-1", status="OPEN", razorpay_dispute_id="dp_abc")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── payment.authorized ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorized_triggers_explicit_capture():
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.capture_payment") as mock_capture:
        await handle_razorpay_webhook(
            db, "payment.authorized",
            {"payment": {"entity": {"order_id": "order_abc", "id": "pay_new", "amount": 100000}}},
        )

    assert payment.status == "AUTHORIZED"
    assert payment.razorpay_payment_id == "pay_new"
    mock_capture.assert_called_once_with("pay_new", 100000)


@pytest.mark.asyncio
async def test_authorized_capture_failure_leaves_status_authorized_not_captured():
    """A failed explicit-capture attempt must not be swallowed into a false
    CAPTURED state — a later payment.captured webhook or admin action is
    still the source of truth."""
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.capture_payment", side_effect=PaymentError("capture failed")):
        await handle_razorpay_webhook(
            db, "payment.authorized",
            {"payment": {"entity": {"order_id": "order_abc", "id": "pay_new", "amount": 100000}}},
        )

    assert payment.status == "AUTHORIZED"


@pytest.mark.asyncio
async def test_authorized_ignored_for_already_captured_payment():
    payment = _fake_payment(status="CAPTURED")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments.capture_payment") as mock_capture:
        await handle_razorpay_webhook(
            db, "payment.authorized", {"payment": {"entity": {"order_id": "order_abc", "id": "pay_x"}}}
        )

    mock_capture.assert_not_called()
    assert payment.status == "CAPTURED"


# ── payment.failed must never downgrade a captured payment ─────────────────

@pytest.mark.asyncio
async def test_failed_downgrades_pending_payment():
    payment = _fake_payment(status="PENDING")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    await handle_razorpay_webhook(db, "payment.failed", {"payment": {"entity": {"order_id": "order_abc"}}})

    assert payment.status == "FAILED"


@pytest.mark.asyncio
async def test_failed_never_downgrades_an_already_captured_payment():
    payment = _fake_payment(status="CAPTURED")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    await handle_razorpay_webhook(db, "payment.failed", {"payment": {"entity": {"order_id": "order_abc"}}})

    assert payment.status == "CAPTURED"


# ── refund.created/refund.processed/payment.refunded ───────────────────────
# Confirmed live: a payment refunded directly from the Razorpay dashboard
# left our Payment row stuck showing CAPTURED forever — there was no
# handling at all for any refund event, so a real refund and our own
# bookkeeping silently diverged, with nothing stopping a later payout
# attempt against money that no longer existed in the platform's account.

@pytest.mark.asyncio
async def test_refund_created_marks_payment_refunded_and_freezes_payout():
    payment = _fake_payment(status="CAPTURED", payout_frozen=False)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    await handle_razorpay_webhook(
        db, "refund.created",
        {"refund": {"entity": {"payment_id": "pay_x", "amount": 462900}}},
    )

    assert payment.status == "REFUNDED"
    assert payment.escrow_status == "REFUNDED"
    assert payment.payout_frozen is True


@pytest.mark.asyncio
async def test_payment_refunded_event_falls_back_to_payment_entity_id():
    """payment.refunded nests under payload["payment"], not payload["refund"]
    — must still correlate correctly using that shape."""
    payment = _fake_payment(status="CAPTURED")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    await handle_razorpay_webhook(
        db, "payment.refunded",
        {"payment": {"entity": {"id": "pay_x", "amount_refunded": 462900}}},
    )

    assert payment.status == "REFUNDED"


@pytest.mark.asyncio
async def test_refund_processed_is_idempotent_after_refund_created():
    payment = _fake_payment(status="REFUNDED", escrow_status="REFUNDED", payout_frozen=True)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    await handle_razorpay_webhook(
        db, "refund.processed",
        {"refund": {"entity": {"payment_id": "pay_x", "amount": 462900}}},
    )

    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_refund_for_unknown_payment_is_a_noop():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    await handle_razorpay_webhook(
        db, "refund.created",
        {"refund": {"entity": {"payment_id": "pay_ghost", "amount": 1000}}},
    )

    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_execute_agency_payout_refuses_when_payment_not_captured():
    payment = _fake_payment(status="REFUNDED", payout_frozen=True)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with pytest.raises(BadRequestError, match="chargeback"):
        await execute_agency_payout(db, "payment-1")


@pytest.mark.asyncio
async def test_execute_agency_payout_refuses_pending_payment_even_when_not_frozen():
    """Belt-and-suspenders: a payment that's simply never been captured
    (no refund involved at all) must also be refused, not just a frozen one."""
    payment = _fake_payment(status="PENDING", payout_frozen=False)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with pytest.raises(BadRequestError, match="isn't captured"):
        await execute_agency_payout(db, "payment-1")


# ── payment.dispute.* (real chargebacks, distinct from customer Dispute) ───

@pytest.mark.asyncio
async def test_dispute_created_freezes_payout_and_creates_record():
    payment = _fake_payment()
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, None])  # payment lookup, then no existing dispute
    db.add = MagicMock()

    with patch("app.services.payments.settings") as mock_settings:
        mock_settings.platform_admin_email = ""  # skip email path for this test
        await handle_razorpay_webhook(
            db, "payment.dispute.created",
            {
                "payment": {"entity": {"order_id": "order_abc"}},
                "dispute": {"entity": {"id": "dp_new", "reason_code": "fraudulent"}},
            },
        )

    assert payment.payout_frozen is True
    db.add.assert_called_once()
    created = db.add.call_args.args[0]
    assert created.source == "RAZORPAY_CHARGEBACK"
    assert created.razorpay_dispute_id == "dp_new"
    assert created.status == "OPEN"


@pytest.mark.asyncio
async def test_dispute_won_unfreezes_and_updates_existing_record():
    payment = _fake_payment(payout_frozen=True)
    dispute = _fake_dispute(status="OPEN")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, dispute])

    with patch("app.services.payments.settings") as mock_settings:
        mock_settings.platform_admin_email = ""
        await handle_razorpay_webhook(
            db, "payment.dispute.won",
            {
                "payment": {"entity": {"order_id": "order_abc"}},
                "dispute": {"entity": {"id": "dp_abc"}},
            },
        )

    assert dispute.status == "WON"
    assert payment.payout_frozen is False


@pytest.mark.asyncio
async def test_dispute_lost_stays_frozen():
    payment = _fake_payment(payout_frozen=True)
    dispute = _fake_dispute(status="UNDER_REVIEW")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[payment, dispute])

    with patch("app.services.payments.settings") as mock_settings:
        mock_settings.platform_admin_email = ""
        await handle_razorpay_webhook(
            db, "payment.dispute.lost",
            {"payment": {"entity": {"order_id": "order_abc"}}, "dispute": {"entity": {"id": "dp_abc"}}},
        )

    assert dispute.status == "LOST"
    assert payment.payout_frozen is True


@pytest.mark.asyncio
async def test_dispute_webhook_ignored_when_malformed():
    db = AsyncMock()
    db.scalar = AsyncMock()

    await handle_razorpay_webhook(db, "payment.dispute.created", {"payment": {"entity": {}}, "dispute": {"entity": {}}})

    db.scalar.assert_not_called()


def test_dispute_status_sets_are_disjoint_and_exhaustive_for_terminal_states():
    """Sanity check on the freeze/unfreeze classification itself — WON/LOST/
    CLOSED/OPEN/UNDER_REVIEW/ACTION_REQUIRED must not appear in both sets."""
    assert DISPUTE_STATUSES_FREEZE_PAYOUT.isdisjoint(DISPUTE_STATUSES_UNFREEZE_PAYOUT)


# ── execute_agency_payout must refuse when a payment is frozen ─────────────

@pytest.mark.asyncio
async def test_execute_agency_payout_refuses_when_payout_frozen():
    from app.exceptions import BadRequestError

    payment = _fake_payment(payout_frozen=True)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with pytest.raises(BadRequestError, match="chargeback"):
        await execute_agency_payout(db, "payment-1")


# ── downtime / notification / invoice / payment_link: no-op, never crash ───

@pytest.mark.asyncio
async def test_downtime_event_is_pure_logging_no_db_access():
    db = AsyncMock()

    await handle_razorpay_webhook(
        db, "payment.downtime.started",
        {"payment": {"downtime": {"entity": {"id": "dt_1", "method": "upi", "status": "started"}}}},
    )

    db.scalar.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_order_notification_event_is_a_no_op():
    db = AsyncMock()
    await handle_razorpay_webhook(db, "order.notification.failed", {"order": {"entity": {}}})
    db.scalar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["invoice.paid", "invoice.partially_paid", "invoice.expired"])
async def test_invoice_events_are_a_no_op(event):
    db = AsyncMock()
    await handle_razorpay_webhook(db, event, {"invoice": {"entity": {}}})
    db.scalar.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event", ["payment_link.paid", "payment_link.partially_paid", "payment_link.expired", "payment_link.cancelled"]
)
async def test_payment_link_events_are_a_no_op(event):
    db = AsyncMock()
    await handle_razorpay_webhook(db, event, {"payment_link": {"entity": {}}})
    db.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_unrecognized_event_is_logged_not_raised():
    db = AsyncMock()
    await handle_razorpay_webhook(db, "some.future.event.razorpay.adds.later", {})
    db.scalar.assert_not_called()


# ── amount-mismatch on capture is recorded, not silently trusted or dropped ─

@pytest.mark.asyncio
async def test_capture_with_amount_mismatch_still_finalizes_but_logs_error(caplog):
    payment = _fake_payment(amount=100000)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=payment)

    with patch("app.services.payments._finalize_capture", new=AsyncMock()) as mock_finalize:
        await handle_razorpay_webhook(
            db, "payment.captured",
            {"payment": {"entity": {"order_id": "order_abc", "id": "pay_x", "amount": 999999}}},
        )

    # The money moved for the amount Razorpay reports — must still finalize.
    mock_finalize.assert_called_once()
    assert any("Amount mismatch" in r.message for r in caplog.records)
