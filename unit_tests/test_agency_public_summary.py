"""Guards against re-introducing the GST/PAN leak on public agency surfaces.

If these fail, someone added gstin/pan back onto AgencyPublicSummary, or
routed a public endpoint through the full AgencySummary again.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.common import AgencyPublicSummary, AgencySummary
from app.schemas.packages import PackageDetails, PackageMeta
from app.schemas.plans import OfferInPlan
from app.services.agencies import (
    _agency_to_public_summary,
    _agency_to_summary,
    _requester_has_agency_access,
)


def test_agency_public_summary_schema_has_no_gstin_or_pan_fields():
    assert "gstin" not in AgencyPublicSummary.model_fields
    assert "pan" not in AgencyPublicSummary.model_fields


def test_agency_summary_schema_still_has_gstin_and_pan_for_authenticated_contexts():
    assert "gstin" in AgencySummary.model_fields
    assert "pan" in AgencySummary.model_fields


def test_package_details_and_meta_use_public_summary():
    assert PackageDetails.model_fields["agency"].annotation is AgencyPublicSummary
    assert PackageMeta.model_fields["agency"].annotation is AgencyPublicSummary


def test_offer_in_plan_uses_public_summary():
    """OfferInPlan is embedded in the public PlanDetails response (offers/
    selectedOffer) — must never carry GST/PAN, unlike schemas/offers.py's
    OfferResponse, which is only returned from authenticated /offers/* routes."""
    assert OfferInPlan.model_fields["agency"].annotation is AgencyPublicSummary


def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1",
        name="Test Agency",
        slug="test-agency",
        logo_url=None,
        description=None,
        verification_status="verified",
        verification_rejection_reason=None,
        gstin="07AAICS1234A1Z9",
        pan="AAICS1234A",
        tourism_license=None,
        address=None,
        phone=None,
        email=None,
        city=None,
        state=None,
        specializations=None,
        destinations=None,
        avg_rating=4.5,
        review_count=10,
        total_trips=5,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_agency_to_public_summary_builder_never_carries_gstin_or_pan():
    agency = _fake_agency()
    summary = _agency_to_public_summary(agency)
    assert not hasattr(summary, "gstin")
    assert not hasattr(summary, "pan")


def test_agency_to_summary_builder_carries_gstin_and_pan():
    agency = _fake_agency()
    summary = _agency_to_summary(agency)
    assert summary.gstin == "07AAICS1234A1Z9"
    assert summary.pan == "AAICS1234A"


@pytest.mark.asyncio
async def test_requester_has_agency_access_true_for_owner():
    agency = _fake_agency(owner_id="owner-1")
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=AssertionError("should not query membership when owner matches"))
    assert await _requester_has_agency_access(db, agency, "owner-1") is True


@pytest.mark.asyncio
async def test_requester_has_agency_access_true_for_active_member():
    agency = _fake_agency(owner_id="owner-1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=SimpleNamespace(id="member-row"))
    assert await _requester_has_agency_access(db, agency, "member-1") is True


@pytest.mark.asyncio
async def test_requester_has_agency_access_false_for_stranger():
    agency = _fake_agency(owner_id="owner-1")
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    assert await _requester_has_agency_access(db, agency, "stranger-1") is False
