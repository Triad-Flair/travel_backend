"""check_expired_plans and send_upcoming_trip_reminders are Celery beat
tasks — plain sync functions decorated with @celery_app.task(bind=True,...)
that spin up their own asyncio event loop internally (_run_async) and open
their own DB session (AsyncSessionLocal), rather than accepting an
injected AsyncSession like every service function in this codebase. That
makes them impossible to drive with @pytest.mark.asyncio (a fresh loop
can't run inside pytest-asyncio's already-running loop) — these tests call
the task function directly from a plain sync test and patch
app.database.AsyncSessionLocal so the task's own `async with
AsyncSessionLocal() as db:` picks up a fake session instead of opening a
real connection.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as database_module
from app.workers.tasks import check_expired_plans, send_upcoming_trip_reminders


class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc_info):
        return False


def _fake_plan(**overrides):
    defaults = dict(id="plan-1", title="Goa Trip", slug="goa-trip", creator_id="user-1", status="OPEN")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _scalars_result(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def test_check_expired_plans_notifies_each_creator_and_flips_status(monkeypatch):
    plan = _fake_plan()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_result([plan]))

    monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(db))

    with patch("app.services.notifications.create_notification", new=AsyncMock()) as mock_notify:
        check_expired_plans()

    assert plan.status == "EXPIRED"
    mock_notify.assert_awaited_once()
    args = mock_notify.await_args.args
    assert args[1] == "user-1"  # creator_id
    assert args[2] == "plan_expired"
    db.commit.assert_awaited_once()


def test_check_expired_plans_is_a_noop_when_nothing_expired(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_result([]))
    monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(db))

    with patch("app.services.notifications.create_notification", new=AsyncMock()) as mock_notify:
        check_expired_plans()

    mock_notify.assert_not_called()


def _fake_member(**overrides):
    defaults = dict(user_id="user-1", status="APPROVED", trip_reminder_sent_at=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_group(**overrides):
    defaults = dict(id="group-1")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_trip(**overrides):
    defaults = dict(id="plan-1", title="Goa Trip", start_date=datetime.now(UTC) + timedelta(days=2), status="CONFIRMED")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_send_upcoming_trip_reminders_notifies_unpaid_member_to_pay(monkeypatch):
    member = _fake_member(status="APPROVED")
    group = _fake_group()
    trip = _fake_trip()

    db = AsyncMock()
    plan_result = _scalars_result([])
    plan_result.all = MagicMock(return_value=[(member, group, trip)])
    pkg_result = _scalars_result([])
    pkg_result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(side_effect=[plan_result, pkg_result])
    monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(db))

    with patch("app.services.notifications.create_notification", new=AsyncMock()) as mock_notify:
        send_upcoming_trip_reminders()

    assert member.trip_reminder_sent_at is not None
    mock_notify.assert_awaited_once()
    args = mock_notify.await_args.args
    assert args[1] == "user-1"
    assert args[2] == "payment_due_reminder"


def test_send_upcoming_trip_reminders_notifies_paid_member_trip_is_soon(monkeypatch):
    member = _fake_member(status="COMMITTED")
    group = _fake_group()
    trip = _fake_trip()

    db = AsyncMock()
    plan_result = _scalars_result([])
    plan_result.all = MagicMock(return_value=[(member, group, trip)])
    pkg_result = _scalars_result([])
    pkg_result.all = MagicMock(return_value=[])
    db.execute = AsyncMock(side_effect=[plan_result, pkg_result])
    monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(db))

    with patch("app.services.notifications.create_notification", new=AsyncMock()) as mock_notify:
        send_upcoming_trip_reminders()

    args = mock_notify.await_args.args
    assert args[2] == "trip_starting_soon"


def test_send_upcoming_trip_reminders_noop_when_nothing_upcoming(monkeypatch):
    db = AsyncMock()
    empty = _scalars_result([])
    empty.all = MagicMock(return_value=[])
    db.execute = AsyncMock(side_effect=[empty, empty])
    monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _FakeSessionCtx(db))

    with patch("app.services.notifications.create_notification", new=AsyncMock()) as mock_notify:
        send_upcoming_trip_reminders()

    mock_notify.assert_not_called()
