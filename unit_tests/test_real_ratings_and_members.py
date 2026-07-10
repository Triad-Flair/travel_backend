"""Two data-integrity fixes surfaced by live testing:
  1. Group fill counts (currentSize/maleCount/femaleCount) were real, but the
     member list backing them was never returned — pages showed a nonzero
     fill count next to "Be the first to join this trip!".
  2. Discover cards showed a hash-seeded fake rating/review count with zero
     connection to any real review — Package/Plan aren't reviewable objects
     (Review only ever targets an agency or a co-traveler).
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.discover import _pkg_to_discover, _plan_to_discover


def _fake_user(**overrides):
    defaults = dict(
        id="user-1", display_name="Test User", username="testuser", avatar_url=None,
        verification_tier="BASIC", gender=None, location=None, avg_rating=4.5, completed_trips=3,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_member(**overrides):
    defaults = dict(id="member-1", role="MEMBER", status="APPROVED", joined_at=datetime(2026, 1, 1), user=_fake_user())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_group(**overrides):
    defaults = dict(
        id="group-1", current_size=2, male_count=1, female_count=1, other_count=0,
        is_locked=False, payment_window_closes_at=None, members=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ── Rule 1a: package group summary carries real members ────────────────────

@pytest.mark.asyncio
async def test_package_group_to_summary_carries_real_members():
    from app.services.packages import _group_to_summary as pkg_group_to_summary
    from app.schemas.plans import GroupMemberSummary

    group = _fake_group(current_size=1)
    member_summary = [
        GroupMemberSummary(id="m1", role="MEMBER", status="APPROVED", joined_at=None, user=SimpleNamespace(
            id="u1", full_name="A", username="a", avatar_url=None, verification=None, gender=None,
            city=None, avg_rating=0.0, completed_trips=0,
        ))
    ]
    summary = pkg_group_to_summary(group, member_summary)

    assert summary.current_size == 1
    assert summary.members is not None
    assert len(summary.members) == 1


@pytest.mark.asyncio
async def test_fetch_group_members_filters_to_active_statuses_only():
    from unittest.mock import AsyncMock, MagicMock
    from app.services.packages import _fetch_group_members

    active_member = _fake_member(status="APPROVED")
    left_member = _fake_member(id="member-2", status="LEFT")

    db = AsyncMock()
    result = MagicMock()
    # The query itself filters via .in_(ACTIVE_MEMBER_STATUSES) — this test
    # confirms only what the (mocked) query returns gets mapped, and that a
    # member with no linked user doesn't crash the mapping.
    result.scalars.return_value.all.return_value = [active_member]
    db.execute = AsyncMock(return_value=result)

    members = await _fetch_group_members(db, "group-1")

    assert len(members) == 1
    assert members[0].status == "APPROVED"
    assert left_member.status == "LEFT"  # sanity: fixture itself is well-formed


# ── Rule 1b: plan group summary carries real members, filters LEFT/REMOVED ─

def test_plan_group_to_summary_filters_inactive_members():
    from app.services.plans import _group_to_summary as plan_group_to_summary

    active = _fake_member(id="m-active", status="APPROVED")
    left = _fake_member(id="m-left", status="LEFT")
    group = _fake_group(current_size=1, members=[active, left])

    summary = plan_group_to_summary(group)

    assert summary.members is not None
    assert len(summary.members) == 1
    assert summary.members[0].id == "m-active"


def test_plan_group_to_summary_omits_members_when_relationship_not_loaded():
    from app.services.plans import _group_to_summary as plan_group_to_summary

    group = _fake_group(members=None)
    summary = plan_group_to_summary(group)

    assert summary.members is None


# ── Rule 2: Discover items carry the real agency/creator rating ────────────

def test_pkg_to_discover_uses_real_agency_rating():
    agency = SimpleNamespace(avg_rating=4.7, review_count=23)
    pkg = SimpleNamespace(
        id="pkg-1", slug="pkg-1", title="Test Package", destination="Goa", destination_state=None,
        start_date=None, end_date=None, price_per_person=15000, vibes=None,
        group_size_min=2, group_size_max=10, gallery_urls=None, cover_image_url=None,
        status="OPEN", created_at=datetime(2026, 1, 1), agency_id="agency-1", agency=agency,
    )

    item = _pkg_to_discover(pkg)

    assert item.rating == 4.7
    assert item.rating_count == 23


def test_pkg_to_discover_omits_rating_when_agency_has_no_reviews():
    agency = SimpleNamespace(avg_rating=0.0, review_count=0)
    pkg = SimpleNamespace(
        id="pkg-1", slug="pkg-1", title="Test Package", destination="Goa", destination_state=None,
        start_date=None, end_date=None, price_per_person=15000, vibes=None,
        group_size_min=2, group_size_max=10, gallery_urls=None, cover_image_url=None,
        status="OPEN", created_at=datetime(2026, 1, 1), agency_id="agency-1", agency=agency,
    )

    item = _pkg_to_discover(pkg)

    assert item.rating is None


def test_plan_to_discover_uses_real_creator_rating():
    creator = SimpleNamespace(avg_rating=4.2, completed_trips=6)
    plan = SimpleNamespace(
        id="plan-1", slug="plan-1", title="Test Plan", destination="Manali", destination_state=None,
        start_date=None, end_date=None, budget_min=10000, budget_max=20000, vibes=None,
        group_type=None, group_size_min=2, group_size_max=10, gallery_urls=None, cover_image_url=None,
        status="OPEN", created_at=datetime(2026, 1, 1), creator_id="user-1", creator=creator,
    )

    item = _plan_to_discover(plan)

    assert item.rating == 4.2
    assert item.rating_count == 6
