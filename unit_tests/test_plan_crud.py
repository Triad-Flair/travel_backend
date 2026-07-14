"""Same bug class caught live in packages.py (publish_package/update_package)
and payments.py (create_payment_order): update_plan mutates fields via
setattr, flushes, then reads plan.updated_at (onupdate=func.now(), expired
by the flush) inside _plan_to_details — a bare synchronous re-read of an
expired attribute crashes with sqlalchemy.exc.MissingGreenlet outside an
async context. update_plan never got the db.refresh(plan) call that
publish_plan already has. Found via an audit of every updated_at.isoformat()
read after this bug hit production three separate times."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.plans import UpdatePlanRequest
from app.services.plans import update_plan


def _fake_user(**overrides):
    defaults = dict(
        id="user-1", display_name="Test User", username="testuser", avatar_url=None,
        verification_tier="BASIC", gender=None, location=None, avg_rating=0.0, completed_trips=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_plan(**overrides):
    now = datetime.now()
    defaults = dict(
        id="plan-1", creator_id="user-1", slug="goa-abc123", title="Goa Trip",
        destination="Goa", destination_state="Goa", start_date=None, end_date=None,
        is_flexible_dates=False, budget_min=1000, budget_max=5000,
        group_size_min=2, group_size_max=10, vibes=[], accommodation=None,
        group_type=None, gender_pref=None, activities=[], description=None,
        itinerary=None, gallery_urls=[], cover_image_url=None, auto_approve=False,
        status="DRAFT", plan_type="STANDARD", expires_at=None, confirmed_at=None,
        confirmed_offer_id=None, creator=_fake_user(), group=None,
        created_at=now, updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_update_plan_refreshes_after_flush_to_avoid_expired_attribute_crash():
    plan = _fake_plan()
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=plan)
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    with patch("app.services.plans.invalidate", new=AsyncMock()):
        details = await update_plan(db, "plan-1", "user-1", UpdatePlanRequest(title="New Title"))

    db.refresh.assert_awaited_once_with(plan)
    assert details.title == "New Title"
