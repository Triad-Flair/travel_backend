"""create_notification is the single new entry point for creating in-app
Notification rows — before it existed, every event type except "new
follower" and a chat compliance reminder had no notification path at all.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.models.social import Notification
from app.services.notifications import create_notification


@pytest.mark.asyncio
async def test_create_notification_adds_and_flushes_a_notification_row():
    db = AsyncMock()

    with patch("app.websockets.socketio_server.emit_to_user", new=AsyncMock()):
        notif = await create_notification(
            db, "user-1", "payment_captured", "Payment successful",
            "Your payment was captured.", href="/dashboard/invoices/pay-1",
        )

    assert isinstance(notif, Notification)
    assert notif.user_id == "user-1"
    assert notif.type == "payment_captured"
    assert notif.title == "Payment successful"
    assert notif.href == "/dashboard/invoices/pay-1"
    db.add.assert_called_once_with(notif)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_notification_pushes_a_live_socket_event():
    db = AsyncMock()

    with patch("app.websockets.socketio_server.emit_to_user", new=AsyncMock()) as mock_emit:
        notif = await create_notification(
            db, "user-1", "referral_earned", "Referral bonus earned!",
            "₹250 was added to your wallet.", href="/dashboard/refer-and-earn",
        )

    mock_emit.assert_awaited_once()
    args = mock_emit.await_args.args
    assert args[0] == "user-1"
    assert args[1] == "notification:created"
    payload = args[2]
    assert payload["id"] == notif.id
    assert payload["type"] == "referral_earned"
    assert payload["read"] is False


@pytest.mark.asyncio
async def test_create_notification_survives_a_socket_push_failure():
    """A Redis/socket hiccup must never take down whatever business
    transaction (payment capture, referral credit, etc.) triggered this
    notification — the DB row still needs to exist even if the live push
    fails."""
    db = AsyncMock()

    with patch("app.websockets.socketio_server.emit_to_user", new=AsyncMock(side_effect=RuntimeError("no redis"))):
        notif = await create_notification(
            db, "user-1", "wallet_balance_changed", "Wallet credit used",
            "₹22 was used at checkout.",
        )

    assert notif.user_id == "user-1"
    db.add.assert_called_once()
