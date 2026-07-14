"""create_package was crashing every submission with a 500 (confirmed live
in production logs): Package.agency is lazy="noload", and
db.refresh(pkg, ["agency"]) is a documented no-op for noload relationships
(it resets the attribute to unloaded rather than querying it), so
_pkg_to_details -> _agency_to_summary(pkg.agency) always hit
AttributeError: 'NoneType' object has no attribute 'id'.

Also fixed in the same pass: create_package/update_package silently dropped
start_date/end_date entirely — the request schema carries them as ISO
strings but the Package(...) constructor never read them.

A second, separate live 500 followed on publish: pkg.status = "OPEN" then
db.flush() marks pkg.updated_at (onupdate=func.now()) expired, and a bare
synchronous re-read of an expired attribute in _pkg_to_details crashes with
sqlalchemy.exc.MissingGreenlet outside an async context. Same class of bug
already fixed once in services/offers.py::counter_offer — publish_package/
update_package just hadn't been given the same db.refresh(pkg) call. A
plain db.refresh(pkg) is safe here (doesn't re-blank the noload agency
relationship) because Session.refresh() reloads relationship attributes
using whatever eager strategy they were originally loaded with — and both
functions load pkg via selectinload(Package.agency) up front.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.packages import CreatePackageRequest, UpdatePackageRequest
from app.services.packages import create_package, publish_package, update_package


def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1", name="Test Agency", slug="test-agency", logo_url=None,
        description=None, verification_status="VERIFIED", phone=None, email=None,
        city=None, state=None, avg_rating=4.5, review_count=10, total_trips=3,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_package(**overrides):
    now = datetime.now()
    defaults = dict(
        id="pkg-1", agency_id="agency-1", slug="dubai-abc123", title="Dubai", destination="Abu Dhabi",
        destination_state="Abu Dhabi", start_date=now, end_date=now,
        departure_dates=[], price_per_person=110, pricing_tiers=None,
        group_size_min=4, group_size_max=14, inclusions=None, exclusions=None,
        accommodation=None, vibes=["Adventure"], activities=["Trekking"],
        gallery_urls=[], cancellation_policy=None, cancellation_rules=None,
        itinerary=[{"day": 1, "title": "Day 1"}], status="DRAFT",
        agency=_fake_agency(), created_at=now, updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_request(**overrides):
    defaults = dict(
        title="Dubai Getaway", destination="Abu Dhabi", destination_state="Abu Dhabi",
        base_price=110, group_size_min=4, group_size_max=14,
        start_date="2026-07-23T03:30:00.000Z", end_date="2026-07-31T03:30:00.000Z",
        departure_dates=[], vibes=["Adventure"], activities=["Trekking"],
        gallery_urls=[], itinerary=[{"day": 1, "title": "Day 1"}],
    )
    defaults.update(overrides)
    return CreatePackageRequest(**defaults)


@pytest.mark.asyncio
async def test_create_package_does_not_crash_on_noload_agency_relationship():
    """Regression test for the live 500: the fix re-queries with
    selectinload instead of relying on refresh(pkg, ["agency"])."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one = MagicMock(return_value=_fake_package())
    db.execute = AsyncMock(return_value=execute_result)

    details = await create_package(db, "agency-1", _make_request())

    assert details.agency.id == "agency-1"
    assert details.agency.name == "Test Agency"


@pytest.mark.asyncio
async def test_create_package_parses_start_and_end_dates():
    db = AsyncMock()
    captured = {}
    db.add = MagicMock(side_effect=lambda obj: captured.__setitem__("pkg", obj))
    db.flush = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one = MagicMock(return_value=_fake_package())
    db.execute = AsyncMock(return_value=execute_result)

    await create_package(db, "agency-1", _make_request())

    inserted = captured["pkg"]
    assert inserted.start_date == datetime.fromisoformat("2026-07-23T03:30:00.000Z")
    assert inserted.end_date == datetime.fromisoformat("2026-07-31T03:30:00.000Z")


@pytest.mark.asyncio
async def test_create_package_allows_missing_dates():
    db = AsyncMock()
    captured = {}
    db.add = MagicMock(side_effect=lambda obj: captured.__setitem__("pkg", obj))
    db.flush = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one = MagicMock(return_value=_fake_package())
    db.execute = AsyncMock(return_value=execute_result)

    await create_package(db, "agency-1", _make_request(start_date=None, end_date=None))

    inserted = captured["pkg"]
    assert inserted.start_date is None
    assert inserted.end_date is None


@pytest.mark.asyncio
async def test_update_package_parses_start_and_end_dates():
    pkg = _fake_package()
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=pkg)
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    with patch("app.services.packages.invalidate", new=AsyncMock()):
        await update_package(
            db, "pkg-1", "agency-1",
            UpdatePackageRequest(start_date="2026-08-01T00:00:00.000Z", end_date="2026-08-10T00:00:00.000Z"),
        )

    assert pkg.start_date == datetime.fromisoformat("2026-08-01T00:00:00.000Z")
    assert pkg.end_date == datetime.fromisoformat("2026-08-10T00:00:00.000Z")


@pytest.mark.asyncio
async def test_update_package_refreshes_pkg_after_flush_to_avoid_expired_attribute_crash():
    """Regression test for the live 500 on the same class of bug the
    publish_package test below covers — a bare access of pkg.updated_at
    after flush (onupdate=func.now()) crashes with MissingGreenlet unless
    explicitly refreshed first."""
    pkg = _fake_package()
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=pkg)
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    with patch("app.services.packages.invalidate", new=AsyncMock()):
        await update_package(db, "pkg-1", "agency-1", UpdatePackageRequest(title="New Title"))

    db.refresh.assert_awaited_once_with(pkg)


@pytest.mark.asyncio
async def test_publish_package_refreshes_pkg_after_flush_to_avoid_expired_attribute_crash():
    """Regression test for the live 500 on POST /packages/{id}/publish:
    sqlalchemy.exc.MissingGreenlet from reading pkg.updated_at (expired by
    the onupdate=func.now() column after flush) outside an async context."""
    pkg = _fake_package(status="DRAFT")
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=pkg)
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    with patch("app.services.packages.invalidate", new=AsyncMock()), \
         patch("app.services.packages.invalidate_pattern", new=AsyncMock()):
        details = await publish_package(db, "pkg-1", "agency-1")

    db.refresh.assert_awaited_once_with(pkg)
    assert details.status == "OPEN"
    assert pkg.status == "OPEN"
