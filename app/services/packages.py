import json
import uuid
from collections.abc import Sequence
from datetime import datetime

from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.cache import CacheKeys, TTL_LONG, get_cached, invalidate, invalidate_pattern, set_cached
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.agency import Agency
from app.models.group import Group, GroupMember
from app.models.package import Package
from app.schemas.common import AgencyCard, AgencyPublicSummary, UserSummary
from app.schemas.groups import GroupSummaryResponse
from app.schemas.packages import CreatePackageRequest, PackageCardResponse, PackageDetails, PackageMeta, UpdatePackageRequest
from app.schemas.plans import GroupMemberSummary, GroupSummary

ACTIVE_MEMBER_STATUSES = ("APPROVED", "COMMITTED")


def _calc_duration(start: datetime | None, end: datetime | None) -> int | None:
    if start and end and end > start:
        return (end - start).days + 1
    return None


def _agency_to_card(agency: Agency) -> AgencyCard:
    return AgencyCard(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        logo_url=agency.logo_url,
        avg_rating=agency.avg_rating,
    )


def _pkg_to_card(pkg: Package) -> PackageCardResponse:
    gallery = _parse_json_list(pkg.gallery_urls) or []
    return PackageCardResponse(
        id=pkg.id,
        slug=pkg.slug,
        title=pkg.title,
        destination=pkg.destination,
        destination_state=pkg.destination_state,
        thumbnail_url=gallery[0] if gallery else None,
        base_price=pkg.price_per_person,
        start_date=pkg.start_date.isoformat() if pkg.start_date else None,
        end_date=pkg.end_date.isoformat() if pkg.end_date else None,
        duration_days=_calc_duration(pkg.start_date, pkg.end_date),
        group_size_min=pkg.group_size_min,
        group_size_max=pkg.group_size_max,
        vibes=_parse_json_list(pkg.vibes),
        status=pkg.status,
        agency=_agency_to_card(pkg.agency),
        avg_rating=pkg.avg_rating,
        review_count=pkg.review_count,
        created_at=pkg.created_at.isoformat(),
    )


def _agency_to_summary(agency: Agency) -> AgencyPublicSummary:
    """Package detail/card endpoints are public and unauthenticated — never
    include GST/PAN here. See AgencyPublicSummary docstring."""
    return AgencyPublicSummary(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        logo_url=agency.logo_url,
        description=agency.description,
        verification=agency.verification_status,
        phone=agency.phone,
        email=agency.email,
        city=agency.city,
        state=agency.state,
        avg_rating=agency.avg_rating,
        total_reviews=agency.review_count,
        total_trips=agency.total_trips,
    )


def _member_user_summary(user) -> UserSummary:
    return UserSummary(
        id=user.id,
        full_name=user.display_name or user.username or "",
        username=user.username,
        avatar_url=user.avatar_url,
        verification=user.verification_tier,
        gender=user.gender,
        city=user.location,
        avg_rating=user.avg_rating,
        completed_trips=user.completed_trips,
    )


async def _fetch_group_members(db: AsyncSession, group_id: str) -> list[GroupMemberSummary]:
    """The group's currentSize/maleCount/femaleCount counters are
    maintained independently of this query (incremented on join) — fetch the
    real roster here so a page never shows a fill count with no one to
    back it up."""
    result = await db.execute(
        select(GroupMember)
        .options(selectinload(GroupMember.user))
        .where(GroupMember.group_id == group_id, GroupMember.status.in_(ACTIVE_MEMBER_STATUSES))
        .order_by(GroupMember.joined_at.asc())
    )
    members = result.scalars().all()
    return [
        GroupMemberSummary(
            id=m.id,
            role=m.role,
            status=m.status,
            joined_at=m.joined_at.isoformat() if m.joined_at else None,
            user=_member_user_summary(m.user),
        )
        for m in members
        if m.user
    ]


def _group_to_summary(group: Group, members: list[GroupMemberSummary] | None = None) -> GroupSummary:
    return GroupSummary(
        id=group.id,
        current_size=group.current_size or 0,
        male_count=group.male_count or 0,
        female_count=group.female_count or 0,
        other_count=group.other_count or 0,
        is_locked=bool(group.is_locked),
        payment_window_ends_at=group.payment_window_closes_at.isoformat() if group.payment_window_closes_at else None,
        members=members,
    )


def _pkg_to_details(
    pkg: Package, group: Group | None = None, members: list[GroupMemberSummary] | None = None
) -> PackageDetails:
    gallery = _parse_json_list(pkg.gallery_urls)
    return PackageDetails(
        id=pkg.id,
        slug=pkg.slug,
        title=pkg.title,
        destination=pkg.destination,
        destination_state=pkg.destination_state,
        start_date=pkg.start_date.isoformat() if pkg.start_date else None,
        end_date=pkg.end_date.isoformat() if pkg.end_date else None,
        departure_dates=_parse_json_list(pkg.departure_dates),
        base_price=pkg.price_per_person,
        pricing_tiers=_parse_json(pkg.pricing_tiers),
        group_size_min=pkg.group_size_min,
        group_size_max=pkg.group_size_max,
        inclusions=_parse_json(pkg.inclusions),
        exclusions=pkg.exclusions,
        accommodation=pkg.accommodation,
        vibes=_parse_json_list(pkg.vibes),
        activities=_parse_json_list(pkg.activities),
        gallery_urls=gallery,
        cancellation_policy=pkg.cancellation_policy,
        cancellation_rules=_parse_json(pkg.cancellation_rules),
        itinerary=_parse_json(pkg.itinerary),
        status=pkg.status,
        agency=_agency_to_summary(pkg.agency),
        group=_group_to_summary(group, members) if group else None,
        created_at=pkg.created_at.isoformat(),
        updated_at=pkg.updated_at.isoformat(),
    )


def _pkg_to_meta(pkg: Package) -> PackageMeta:
    gallery = _parse_json_list(pkg.gallery_urls)
    return PackageMeta(
        id=pkg.id,
        slug=pkg.slug,
        title=pkg.title,
        destination=pkg.destination,
        destination_state=pkg.destination_state,
        start_date=pkg.start_date.isoformat() if pkg.start_date else None,
        end_date=pkg.end_date.isoformat() if pkg.end_date else None,
        base_price=pkg.price_per_person,
        group_size_min=pkg.group_size_min,
        group_size_max=pkg.group_size_max,
        gallery_urls=gallery,
        vibes=_parse_json_list(pkg.vibes),
        status=pkg.status,
        agency=_agency_to_summary(pkg.agency),
        created_at=pkg.created_at.isoformat(),
    )


def _parse_json(raw: object) -> dict | list | None:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")
    if not isinstance(raw, str):
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_json_list(raw: object) -> list | None:
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [v for v in raw]
    if isinstance(raw, str) and raw and not raw.strip().startswith("["):
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        return parts or None
    val = _parse_json(raw)
    return val if isinstance(val, list) else None


async def get_package_by_slug(db: AsyncSession, slug: str) -> PackageDetails:
    cache_key = CacheKeys.package_detail(slug)
    cached = await get_cached(cache_key)
    if cached:
        return PackageDetails(**cached)

    result = await db.execute(
        select(Package)
        .options(selectinload(Package.agency))
        .where(Package.slug == slug)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise NotFoundError("Package")

    group = await db.scalar(select(Group).where(Group.package_id == pkg.id))
    members = await _fetch_group_members(db, group.id) if group else None
    details = _pkg_to_details(pkg, group, members)
    await set_cached(cache_key, details.model_dump(by_alias=True), TTL_LONG)
    return details


async def get_package_by_id(db: AsyncSession, pkg_id: str) -> PackageDetails:
    result = await db.execute(
        select(Package)
        .options(selectinload(Package.agency))
        .where(Package.id == pkg_id)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise NotFoundError("Package")
    group = await db.scalar(select(Group).where(Group.package_id == pkg.id))
    members = await _fetch_group_members(db, group.id) if group else None
    return _pkg_to_details(pkg, group, members)


async def list_my_packages(
    db: AsyncSession, agency_id: str, page: int, page_size: int
) -> tuple[list[PackageCardResponse], int]:
    total = await db.scalar(
        select(func.count(Package.id)).where(Package.agency_id == agency_id)
    ) or 0
    result = await db.execute(
        select(Package)
        .options(
            # Only load columns the card needs — defers heavy JSONB fields
            # (itinerary, inclusions, cancellation_rules, pricing_tiers, etc.)
            load_only(
                Package.id, Package.slug, Package.title, Package.destination,
                Package.destination_state, Package.price_per_person, Package.start_date,
                Package.end_date, Package.group_size_min, Package.group_size_max,
                Package.gallery_urls, Package.vibes, Package.status, Package.agency_id,
                Package.created_at,
            ),
            selectinload(Package.agency),
        )
        .where(Package.agency_id == agency_id)
        .order_by(Package.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [_pkg_to_card(p) for p in result.scalars().all()], total


async def create_package(db: AsyncSession, agency_id: str, req: CreatePackageRequest) -> PackageDetails:
    slug = slugify(req.title) + "-" + str(uuid.uuid4())[:8]
    pkg = Package(
        id=str(uuid.uuid4()),
        agency_id=agency_id,
        title=req.title,
        slug=slug,
        destination=req.destination,
        destination_state=req.destination_state,
        start_date=datetime.fromisoformat(req.start_date) if req.start_date else None,
        end_date=datetime.fromisoformat(req.end_date) if req.end_date else None,
        price_per_person=req.base_price,
        group_size_min=req.group_size_min,
        group_size_max=req.group_size_max,
        accommodation=req.accommodation,
        gallery_urls=req.gallery_urls or None,
        vibes=req.vibes or None,
        activities=req.activities or None,
        inclusions=req.inclusions or None,
        exclusions=req.exclusions,
        cancellation_policy=req.cancellation_policy,
        cancellation_rules=req.cancellation_rules or None,
        itinerary=req.itinerary or None,
        pricing_tiers=req.pricing_tiers or None,
        departure_dates=req.departure_dates or None,
        status="DRAFT",
    )
    db.add(pkg)
    await db.flush()
    # Package.agency is lazy="noload" — db.refresh(pkg, ["agency"]) is a
    # documented no-op for noload relationships (it resets to unloaded
    # rather than querying), so it must be fetched via an explicit
    # selectinload query instead, same as update_package/publish_package do.
    result = await db.execute(
        select(Package).options(selectinload(Package.agency)).where(Package.id == pkg.id)
    )
    pkg = result.scalar_one()
    await invalidate_pattern("package:*")
    return _pkg_to_details(pkg)


async def update_package(
    db: AsyncSession, pkg_id: str, agency_id: str, req: UpdatePackageRequest
) -> PackageDetails:
    result = await db.execute(
        select(Package).options(selectinload(Package.agency)).where(Package.id == pkg_id)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise NotFoundError("Package")
    if pkg.agency_id != agency_id:
        raise ForbiddenError()

    update_data = req.model_dump(exclude_none=True)
    for field, value in update_data.items():
        json_fields = {"gallery_urls", "vibes", "activities", "inclusions",
                       "cancellation_rules", "itinerary", "pricing_tiers", "departure_dates"}
        if field in json_fields:
            setattr(pkg, _to_model_field(field), value)
        elif field == "base_price":
            pkg.price_per_person = value
        elif field in {"start_date", "end_date"}:
            setattr(pkg, field, datetime.fromisoformat(value) if value else None)
        else:
            db_field = _to_model_field(field)
            if hasattr(pkg, db_field):
                setattr(pkg, db_field, value)

    await db.flush()
    # pkg.updated_at has onupdate=func.now() — SQLAlchemy marks it expired
    # after this UPDATE rather than eagerly re-fetching it, and a bare
    # synchronous re-read of an expired attribute crashes under the asyncio
    # extension (MissingGreenlet). An explicit awaited refresh avoids that —
    # see the identical comment in services/offers.py::counter_offer.
    await db.refresh(pkg)
    await invalidate(CacheKeys.package_detail(pkg.slug))
    return _pkg_to_details(pkg)


async def publish_package(db: AsyncSession, pkg_id: str, agency_id: str) -> PackageDetails:
    result = await db.execute(
        select(Package).options(selectinload(Package.agency)).where(Package.id == pkg_id)
    )
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise NotFoundError("Package")
    if pkg.agency_id != agency_id:
        raise ForbiddenError()
    pkg.status = "OPEN"
    await db.flush()
    # See the onupdate=func.now() / MissingGreenlet comment in update_package.
    await db.refresh(pkg)
    await invalidate(CacheKeys.package_detail(pkg.slug))
    await invalidate_pattern("discover:*")
    return _pkg_to_details(pkg)


def _group_to_summary_response(group: Group) -> GroupSummaryResponse:
    return GroupSummaryResponse(
        id=group.id,
        plan_id=group.plan_id,
        package_id=group.package_id,
        current_size=group.current_size or 0,
        male_count=group.male_count or 0,
        female_count=group.female_count or 0,
        other_count=group.other_count or 0,
        is_locked=bool(group.is_locked),
        payment_window_ends_at=group.payment_window_closes_at.isoformat() if group.payment_window_closes_at else None,
    )


async def book_package(db: AsyncSession, pkg_id: str, user_id: str) -> GroupSummaryResponse:
    """Creates (or joins) the Group for a catalog package — the missing link
    that made packages unbookable: nothing else in the codebase creates a
    Group for a Package. One Group per package at a time, matching how
    services/payments.py::_finalize_capture transitions package.status based
    on a single group's captured-payment count; later bookers of the same
    package join the existing group instead of starting a competing one."""
    pkg = await db.scalar(select(Package).where(Package.id == pkg_id))
    if not pkg:
        raise NotFoundError("Package")
    if pkg.status != "OPEN":
        raise BadRequestError("This package is not currently open for booking")

    group = await db.scalar(select(Group).where(Group.package_id == pkg_id))
    if not group:
        group = Group(id=str(uuid.uuid4()), package_id=pkg_id, current_size=0)
        db.add(group)
        await db.flush()

    if group.is_locked:
        raise BadRequestError("This group is locked and no longer accepting new members")

    member = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user_id)
    )
    if member and member.status in ("APPROVED", "COMMITTED", "INTERESTED"):
        return _group_to_summary_response(group)

    if member:
        member.status = "APPROVED"
        member.joined_at = datetime.utcnow()
        member.left_at = None
    else:
        db.add(
            GroupMember(
                id=str(uuid.uuid4()),
                group_id=group.id,
                user_id=user_id,
                role="CREATOR" if (group.current_size or 0) == 0 else "MEMBER",
                status="APPROVED",
                joined_at=datetime.utcnow(),
            )
        )

    group.current_size = (group.current_size or 0) + 1
    await db.flush()
    await invalidate(CacheKeys.package_detail(pkg.slug))

    # PRD trigger: send_group_chat_verification_email
    from app.workers.tasks import send_group_chat_verification_email_task
    send_group_chat_verification_email_task.delay(user_id, group.id)

    return _group_to_summary_response(group)


def _to_model_field(schema_field: str) -> str:
    mapping = {
        "gallery_urls": "gallery_urls",
        "base_price": "price_per_person",
        "group_size_min": "group_size_min",
        "group_size_max": "group_size_max",
        "destination_state": "destination_state",
        "departure_dates": "departure_dates",
        "pricing_tiers": "pricing_tiers",
        "cancellation_policy": "cancellation_policy",
        "cancellation_rules": "cancellation_rules",
    }
    return mapping.get(schema_field, schema_field)
