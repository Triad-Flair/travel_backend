import json
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import CacheKeys, TTL_MEDIUM, TTL_SHORT, get_cached, set_cached
from app.models.enums import PackageStatus, PlanStatus
from app.models.group import Group, GroupMember
from app.models.package import Package
from app.models.plan import Plan
from app.models.social import Follow
from app.schemas.discover import DiscoverFilters, DiscoverItem, SearchResult

_ACTIVE_MEMBER_STATUSES = ("APPROVED", "COMMITTED")


def _json_list(raw: object) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [str(v) for v in raw if v is not None and str(v).strip()]
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")
    if isinstance(raw, str):
        if not raw.strip():
            return None
        # tolerate comma-separated fallback from legacy text fields
        if raw.strip().startswith("["):
            try:
                value = json.loads(raw)
                if isinstance(value, list):
                    return [str(v) for v in value if v is not None and str(v).strip()]
            except Exception:
                return None
            return None
        values = [part.strip() for part in raw.split(",") if part.strip()]
        return values or None
    try:
        value = json.loads(str(raw))
        if isinstance(value, list):
            return [str(v) for v in value]
    except Exception:
        return None
    return None


def _plan_to_discover(plan: Plan, joined_count: int = 0) -> DiscoverItem:
    gallery = _json_list(plan.gallery_urls) or []
    cover_image = plan.cover_image_url or (gallery[0] if gallery else None)
    return DiscoverItem(
        id=plan.id,
        slug=plan.slug,
        origin_type="plan",
        title=plan.title,
        destination=plan.destination,
        destination_state=plan.destination_state,
        start_date=plan.start_date.isoformat() if plan.start_date else None,
        end_date=plan.end_date.isoformat() if plan.end_date else None,
        price_low=plan.budget_min,
        price_high=plan.budget_max,
        vibes=_json_list(plan.vibes),
        group_type=plan.group_type,
        group_size_min=plan.group_size_min,
        group_size_max=plan.group_size_max,
        cover_image_url=cover_image,
        status=plan.status,
        created_at=plan.created_at.isoformat(),
        owner_id=plan.creator_id,
        agency_id=None,
        joined_count=joined_count,
    )


def _pkg_to_discover(pkg: Package, joined_count: int = 0) -> DiscoverItem:
    gallery = _json_list(pkg.gallery_urls) or []
    cover_image = pkg.cover_image_url or (gallery[0] if gallery else None)
    return DiscoverItem(
        id=pkg.id,
        slug=pkg.slug,
        origin_type="package",
        title=pkg.title,
        destination=pkg.destination,
        destination_state=pkg.destination_state,
        start_date=pkg.start_date.isoformat() if pkg.start_date else None,
        end_date=pkg.end_date.isoformat() if pkg.end_date else None,
        price_low=pkg.price_per_person,
        price_high=pkg.price_per_person,
        vibes=_json_list(pkg.vibes),
        group_size_min=pkg.group_size_min,
        group_size_max=pkg.group_size_max,
        cover_image_url=cover_image,
        status=pkg.status,
        created_at=pkg.created_at.isoformat(),
        owner_id=None,
        agency_id=pkg.agency_id,
        joined_count=joined_count,
    )


async def _batch_plan_joined_counts(db: AsyncSession, plan_ids: list[str]) -> dict[str, int]:
    """One query: count active members per plan via the groups junction."""
    if not plan_ids:
        return {}
    rows = await db.execute(
        select(Group.plan_id, func.count(GroupMember.id).label("cnt"))
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            Group.plan_id.in_(plan_ids),
            GroupMember.status.in_(_ACTIVE_MEMBER_STATUSES),
        )
        .group_by(Group.plan_id)
    )
    return {row.plan_id: row.cnt for row in rows if row.plan_id}


async def _batch_pkg_joined_counts(db: AsyncSession, pkg_ids: list[str]) -> dict[str, int]:
    """One query: count active members per package group."""
    if not pkg_ids:
        return {}
    rows = await db.execute(
        select(Group.package_id, func.count(GroupMember.id).label("cnt"))
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            Group.package_id.in_(pkg_ids),
            GroupMember.status.in_(_ACTIVE_MEMBER_STATUSES),
        )
        .group_by(Group.package_id)
    )
    return {row.package_id: row.cnt for row in rows if row.package_id}


async def get_discover_feed(
    db: AsyncSession, filters: DiscoverFilters, requesting_agency_id: str | None = None
) -> list[DiscoverItem]:
    """requesting_agency_id: when the caller is an agency, Discover shows
    only Plans (User + Corporate — there is no third plan_type) — Packages
    are other agencies' own catalog listings, not leads an agency should be
    browsing (PRD 2.3). Filtered at the query level below, not by dropping
    rows after fetch, so it can't be bypassed by pagination math."""
    import hashlib
    filters_hash = hashlib.md5(filters.model_dump_json().encode()).hexdigest()[:12]
    audience_marker = "agency" if requesting_agency_id else "public"
    cache_key = CacheKeys.discover_feed(filters.page, f"{filters_hash}:{audience_marker}")

    cached = await get_cached(cache_key)
    if cached:
        return [DiscoverItem(**i) for i in cached]

    raw_plans: list[Plan] = []
    raw_pkgs: list[Package] = []
    half = filters.page_size // 2
    offset = (filters.page - 1) * filters.page_size

    show_packages = not requesting_agency_id

    if filters.origin_type in (None, "plan"):
        q = select(Plan).where(Plan.status == 'OPEN')
        if filters.plan_type:
            q = q.where(Plan.plan_type == filters.plan_type)
        if filters.destination:
            q = q.where(Plan.destination.ilike(f"%{filters.destination}%"))
        if filters.budget_min:
            q = q.where(Plan.budget_max >= filters.budget_min)
        if filters.budget_max:
            q = q.where(Plan.budget_min <= filters.budget_max)
        sort = filters.sort or "recent"
        if sort == "price_low":
            q = q.order_by(Plan.budget_min.asc().nullslast())
        elif sort == "price_high":
            q = q.order_by(Plan.budget_max.desc().nullslast())
        else:
            q = q.order_by(Plan.created_at.desc())
        # An agency viewer gets no package results at all (see show_packages
        # below), so give the plan query the full page rather than half.
        limit = filters.page_size if (filters.origin_type == "plan" or not show_packages) else half
        off = offset if (filters.origin_type == "plan" or not show_packages) else 0
        result = await db.execute(q.offset(off).limit(limit))
        raw_plans = list(result.scalars().all())

    if show_packages and filters.origin_type in (None, "package"):
        q = select(Package).where(Package.status == 'OPEN')
        if filters.destination:
            q = q.where(Package.destination.ilike(f"%{filters.destination}%"))
        if filters.budget_min:
            q = q.where(Package.price_per_person >= filters.budget_min)
        if filters.budget_max:
            q = q.where(Package.price_per_person <= filters.budget_max)
        sort = filters.sort or "popular"
        if sort == "price_low":
            q = q.order_by(Package.price_per_person.asc())
        elif sort == "price_high":
            q = q.order_by(Package.price_per_person.desc())
        else:
            q = q.order_by(Package.created_at.desc())
        limit = filters.page_size if filters.origin_type == "package" else half
        off = offset if filters.origin_type == "package" else 0
        result = await db.execute(q.offset(off).limit(limit))
        raw_pkgs = list(result.scalars().all())

    # Batch-count active members — 2 queries regardless of result set size
    plan_counts = await _batch_plan_joined_counts(db, [p.id for p in raw_plans])
    pkg_counts = await _batch_pkg_joined_counts(db, [p.id for p in raw_pkgs])

    items: list[DiscoverItem] = [
        _plan_to_discover(p, plan_counts.get(p.id, 0)) for p in raw_plans
    ] + [
        _pkg_to_discover(p, pkg_counts.get(p.id, 0)) for p in raw_pkgs
    ]

    # Interleave plans and packages when showing both
    if filters.origin_type is None:
        plans = [i for i in items if i.origin_type == "plan"]
        pkgs = [i for i in items if i.origin_type == "package"]
        interleaved: list[DiscoverItem] = []
        for i in range(max(len(plans), len(pkgs))):
            if i < len(plans):
                interleaved.append(plans[i])
            if i < len(pkgs):
                interleaved.append(pkgs[i])
        items = interleaved[: filters.page_size]

    await set_cached(cache_key, [i.model_dump(by_alias=True) for i in items], TTL_MEDIUM)
    return items


async def get_trending(
    db: AsyncSession, page: int, page_size: int, requesting_agency_id: str | None = None
) -> list[DiscoverItem]:
    if requesting_agency_id:
        # Trending is packages-only — an agency viewer has nothing to see
        # here by the same rule as get_discover_feed (PRD 2.3).
        return []

    cache_key = CacheKeys.trending(page)
    cached = await get_cached(cache_key)
    if cached:
        return [DiscoverItem(**i) for i in cached]

    result = await db.execute(
        select(Package)
        .where(Package.status == 'OPEN')
        .order_by(Package.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_pkg_to_discover(p) for p in result.scalars().all()]
    await set_cached(cache_key, [i.model_dump(by_alias=True) for i in items], TTL_SHORT)
    return items


async def search(
    db: AsyncSession, q: str, page: int, page_size: int, requesting_agency_id: str | None = None
) -> list[DiscoverItem]:
    term = f"%{q}%"
    items: list[DiscoverItem] = []
    show_packages = not requesting_agency_id

    plan_result = await db.execute(
        select(Plan)
        .where(
            Plan.status == 'OPEN',
            (Plan.title.ilike(term) | Plan.destination.ilike(term)),
        )
        .order_by(Plan.created_at.desc())
        .limit(page_size if not show_packages else page_size // 2)
    )
    for plan in plan_result.scalars().all():
        items.append(_plan_to_discover(plan))

    if show_packages:
        pkg_result = await db.execute(
            select(Package)
            .where(
                Package.status == 'OPEN',
                (Package.title.ilike(term) | Package.destination.ilike(term)),
            )
            .order_by(Package.created_at.desc())
            .limit(page_size // 2)
        )
        for pkg in pkg_result.scalars().all():
            items.append(_pkg_to_discover(pkg))

    return items


async def get_following_feed(
    db: AsyncSession,
    user_id: str,
    q: str,
    filters: DiscoverFilters,
) -> list[DiscoverItem]:
    follows_result = await db.execute(
        select(Follow).where(Follow.follower_user_id == user_id)
    )
    follows = follows_result.scalars().all()
    followed_user_ids = [f.target_user_id for f in follows if f.target_type == "USER" and f.target_user_id]
    followed_agency_ids = [f.target_agency_id for f in follows if f.target_type == "AGENCY" and f.target_agency_id]

    if not followed_user_ids and not followed_agency_ids:
        return []

    term = f"%{q.strip()}%" if q.strip() else None
    items: list[DiscoverItem] = []

    if filters.origin_type in (None, "plan") and followed_user_ids:
        plan_query = select(Plan).where(
            Plan.creator_id.in_(followed_user_ids),
            Plan.status == "OPEN",
        )
        if filters.destination:
            plan_query = plan_query.where(Plan.destination.ilike(f"%{filters.destination}%"))
        if term:
            plan_query = plan_query.where((Plan.title.ilike(term)) | (Plan.destination.ilike(term)))

        if (filters.sort or "recent") == "price_low":
            plan_query = plan_query.order_by(Plan.budget_min.asc().nullslast(), Plan.created_at.desc())
        elif (filters.sort or "recent") == "price_high":
            plan_query = plan_query.order_by(Plan.budget_max.desc().nullslast(), Plan.created_at.desc())
        else:
            plan_query = plan_query.order_by(Plan.created_at.desc())

        rows = await db.execute(
            plan_query
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        for plan in rows.scalars().all():
            items.append(_plan_to_discover(plan))

    if filters.origin_type in (None, "package") and followed_agency_ids:
        package_query = select(Package).where(
            Package.agency_id.in_(followed_agency_ids),
            Package.status == "OPEN",
        )
        if filters.destination:
            package_query = package_query.where(Package.destination.ilike(f"%{filters.destination}%"))
        if term:
            package_query = package_query.where((Package.title.ilike(term)) | (Package.destination.ilike(term)))

        if (filters.sort or "recent") == "price_low":
            package_query = package_query.order_by(Package.price_per_person.asc(), Package.created_at.desc())
        elif (filters.sort or "recent") == "price_high":
            package_query = package_query.order_by(Package.price_per_person.desc(), Package.created_at.desc())
        else:
            package_query = package_query.order_by(Package.created_at.desc())

        rows = await db.execute(
            package_query
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        for package in rows.scalars().all():
            items.append(_pkg_to_discover(package))

    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[: filters.page_size]
