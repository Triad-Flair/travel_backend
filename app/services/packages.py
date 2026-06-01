import json
import uuid
from collections.abc import Sequence
from datetime import datetime

from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, selectinload

from app.core.cache import CacheKeys, TTL_LONG, get_cached, invalidate, invalidate_pattern, set_cached
from app.exceptions import ForbiddenError, NotFoundError
from app.models.agency import Agency
from app.models.package import Package
from app.schemas.common import AgencyCard, AgencySummary
from app.schemas.packages import CreatePackageRequest, PackageCardResponse, PackageDetails, PackageMeta, UpdatePackageRequest


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


def _agency_to_summary(agency: Agency) -> AgencySummary:
    return AgencySummary(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        logo_url=agency.logo_url,
        description=agency.description,
        verification=agency.verification_status,
        gstin=agency.gstin,
        pan=agency.pan,
        phone=agency.phone,
        email=agency.email,
        city=agency.city,
        state=agency.state,
        avg_rating=agency.avg_rating,
        total_reviews=agency.review_count,
        total_trips=agency.total_trips,
    )


def _pkg_to_details(pkg: Package) -> PackageDetails:
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

    details = _pkg_to_details(pkg)
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
    return _pkg_to_details(pkg)


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
    await db.refresh(pkg, ["agency"])
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
        else:
            db_field = _to_model_field(field)
            if hasattr(pkg, db_field):
                setattr(pkg, db_field, value)

    await db.flush()
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
    await invalidate(CacheKeys.package_detail(pkg.slug))
    await invalidate_pattern("discover:*")
    return _pkg_to_details(pkg)


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
