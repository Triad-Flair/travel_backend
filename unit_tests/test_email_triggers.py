"""PRD section 5 — confirms each trigger point actually dispatches its
Celery task with the right arguments. The task bodies themselves (DB
lookups, template rendering) are exercised via live verification, not here —
these tests are about wiring, not delivery."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.offers import SubmitOfferRequest
from app.services.agencies import approve_verification
from app.services.offers import submit_offer


@pytest.mark.asyncio
async def test_submit_offer_dispatches_bid_alert_email():
    plan = SimpleNamespace(id="plan-1", status="OPEN")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[plan, None])  # plan lookup, no existing offer

    req = SubmitOfferRequest(plan_id="plan-1", price_per_person=5000)

    with patch("app.services.offers._offer_to_response", new=AsyncMock(return_value="response")), \
         patch("app.workers.tasks.send_bid_alert_email_task.delay") as mock_delay:
        result = await submit_offer(db, "agency-1", "user-1", req)

    assert result == "response"
    mock_delay.assert_called_once()
    dispatched_offer_id = mock_delay.call_args.args[0]
    added_offers = [call.args[0] for call in db.add.call_args_list if hasattr(call.args[0], "price_per_person")]
    assert dispatched_offer_id == added_offers[0].id


@pytest.mark.asyncio
async def test_submit_review_endpoint_dispatches_review_alert_email():
    """The live review endpoint is POST /reviews (submit_review_endpoint in
    app/api/v1/reviews.py) — a separate, inline implementation from the
    unreachable app/services/social.py::submit_review. Discovered via live
    verification: hitting the real endpoint showed services/social.py's
    version was never actually called."""
    from app.api.v1.reviews import CreateReviewRequest, submit_review_endpoint

    group = SimpleNamespace(id="group-1")
    agency = SimpleNamespace(
        id="agency-1", name="Test Agency", slug="test-agency", logo_url=None, description=None,
        verification_status="verified", gstin=None, pan=None, tourism_license=None, address=None,
        phone=None, email=None, city=None, state=None, specializations=None, destinations=None,
        avg_rating=0.0, review_count=0, total_trips=0, owner_id="agency-owner-1",
    )
    reviewer = SimpleNamespace(
        id="user-1", display_name="Reviewer", username="reviewer", avatar_url=None,
        verification_tier="BASIC", gender=None, location=None, avg_rating=0.0, completed_trips=0,
    )

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[None, reviewer, agency])  # duplicate-check, reviewer, target_agency
    db.execute = AsyncMock(return_value=MagicMock(one=MagicMock(return_value=(4.5, 3))))

    current_user = SimpleNamespace(user_id="user-1")
    req = CreateReviewRequest(
        group_id="group-1", review_type="agency", target_agency_id="agency-1",
        overall_rating=5, safety_rating=5, value_rating=5, comment="Great trip",
    )

    with patch("app.api.v1.reviews._resolve_group_context", new=AsyncMock(return_value=(group, None, None, agency, None))), \
         patch("app.api.v1.reviews._assert_member", new=AsyncMock()), \
         patch("app.api.v1.reviews._is_trip_over", return_value=True), \
         patch("app.workers.tasks.send_review_alert_email_task.delay") as mock_delay:
        await submit_review_endpoint(req, current_user, db)

    mock_delay.assert_called_once()


@pytest.mark.asyncio
async def test_approve_verification_dispatches_compliance_approval_email():
    agency = SimpleNamespace(id="agency-1", slug="agency-1", owner_id="owner-1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=agency)

    with patch("app.services.agencies._agency_to_summary", return_value="summary"), \
         patch("app.services.agencies.invalidate", new=AsyncMock()), \
         patch("app.workers.tasks.send_compliance_approval_email_task.delay") as mock_delay:
        result = await approve_verification(db, "agency-1")

    assert result == "summary"
    assert agency.verification_status == "verified"
    mock_delay.assert_called_once_with("agency-1")
