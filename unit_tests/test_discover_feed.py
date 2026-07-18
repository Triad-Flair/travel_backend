"""Discover feed was showing far fewer listings than actually exist, for
two independent reasons:

1. Plan.status == 'OPEN' excluded any plan with even one paying traveler —
   payments.py flips a plan to CONFIRMING the instant the first traveler
   pays (same lifecycle event Packages already handled correctly here).
2. get_discover_feed() capped each of the plan/package queries at
   page_size // 2 regardless of how much inventory actually existed on
   either side, so a thin package catalog silently starved the whole feed
   instead of backfilling with more plans.

Also verifies the vibes/groupType filters (accepted by the schema and API
route, but never referenced in any WHERE clause) are now actually applied.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.discover import DiscoverFilters
from app.services.discover import get_discover_feed, search


def _fake_plan(**overrides):
    defaults = dict(
        id="plan-1", slug="plan-1-slug", title="Goa Trip", destination="Goa",
        destination_state=None, start_date=None, end_date=None,
        budget_min=5000, budget_max=10000, vibes=["Beach"], group_type="FRIENDS",
        group_size_min=2, group_size_max=10, cover_image_url=None, gallery_urls=None,
        status="OPEN", created_at=__import__("datetime").datetime(2026, 1, 1),
        creator_id="user-1", creator=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_pkg(**overrides):
    defaults = dict(
        id="pkg-1", slug="pkg-1-slug", title="Manali Package", destination="Manali",
        destination_state=None, start_date=None, end_date=None,
        price_per_person=8000, vibes=["Mountains"], group_size_min=2, group_size_max=14,
        cover_image_url=None, gallery_urls=None, status="OPEN",
        created_at=__import__("datetime").datetime(2026, 1, 1), agency_id="agency-1", agency=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _empty_scalar_result():
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


def _scalar_result(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _row_result(rows=()):
    result = MagicMock()
    result.__iter__ = lambda self: iter(rows)
    return result


@pytest.mark.asyncio
async def test_plan_query_includes_confirming_status(monkeypatch):
    """Regression guard: the exact bug that made a plan disappear from
    Discover the instant its first traveler paid."""
    captured_queries = []

    async def _execute(query, *a, **kw):
        captured_queries.append(query)
        return _empty_scalar_result() if len(captured_queries) <= 2 else _row_result()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    monkeypatch.setattr("app.services.discover.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.discover.set_cached", AsyncMock())

    filters = DiscoverFilters(page=1, page_size=20)
    await get_discover_feed(db, filters, requesting_agency_id=None)

    plan_query_sql = str(captured_queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "'OPEN'" in plan_query_sql
    assert "'CONFIRMING'" in plan_query_sql


@pytest.mark.asyncio
async def test_backfills_plans_when_packages_are_thin(monkeypatch):
    """20 plans available, only 1 package — the feed must not be capped at
    10 plans + 1 package just because it split page_size in half up front."""
    plans = [_fake_plan(id=f"plan-{i}") for i in range(20)]
    pkgs = [_fake_pkg(id="pkg-1")]

    call_count = {"n": 0}

    async def _execute(query, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _scalar_result(plans)
        if call_count["n"] == 2:
            return _scalar_result(pkgs)
        return _row_result()  # batch joined-count queries

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    monkeypatch.setattr("app.services.discover.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.discover.set_cached", AsyncMock())

    filters = DiscoverFilters(page=1, page_size=20)
    items = await get_discover_feed(db, filters, requesting_agency_id=None)

    assert len(items) == 20
    assert sum(1 for i in items if i.origin_type == "package") == 1
    assert sum(1 for i in items if i.origin_type == "plan") == 19


@pytest.mark.asyncio
async def test_vibes_filter_is_applied_to_plan_query(monkeypatch):
    captured_queries = []

    async def _execute(query, *a, **kw):
        captured_queries.append(query)
        return _empty_scalar_result() if len(captured_queries) <= 2 else _row_result()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    monkeypatch.setattr("app.services.discover.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.discover.set_cached", AsyncMock())

    filters = DiscoverFilters(page=1, page_size=20, vibes="Adventure")
    await get_discover_feed(db, filters, requesting_agency_id=None)

    compiled = captured_queries[0].compile()
    assert ["Adventure"] in compiled.params.values()


@pytest.mark.asyncio
async def test_group_type_filter_is_applied_to_plan_query(monkeypatch):
    captured_queries = []

    async def _execute(query, *a, **kw):
        captured_queries.append(query)
        return _empty_scalar_result() if len(captured_queries) <= 2 else _row_result()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    monkeypatch.setattr("app.services.discover.get_cached", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.discover.set_cached", AsyncMock())

    filters = DiscoverFilters(page=1, page_size=20, group_type="FRIENDS")
    await get_discover_feed(db, filters, requesting_agency_id=None)

    plan_query_sql = str(captured_queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "FRIENDS" in plan_query_sql


@pytest.mark.asyncio
async def test_search_backfills_plans_when_packages_are_thin(monkeypatch):
    plans = [_fake_plan(id=f"plan-{i}") for i in range(10)]
    pkgs: list = []

    call_count = {"n": 0}

    async def _execute(query, *a, **kw):
        call_count["n"] += 1
        return _scalar_result(plans) if call_count["n"] == 1 else _scalar_result(pkgs)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    items = await search(db, "goa", page=1, page_size=20, requesting_agency_id=None)

    assert len(items) == 10
