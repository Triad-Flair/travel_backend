import json
import uuid
from datetime import UTC, datetime, timedelta
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import CacheKeys, TTL_LONG, get_cached, invalidate, invalidate_pattern, set_cached
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.group import Group, GroupMember
from app.models.offer import Offer
from app.models.plan import Plan
from app.models.user import User
from app.schemas.common import UserSummary
from app.schemas.plans import (
    ConfirmPlanRequest,
    CreatePlanRequest,
    GroupMemberSummary,
    GroupSummary,
    ItineraryItem,
    OfferInPlan,
    PlanDetails,
    PlanMeta,
    ReferPlanRequest,
    UpdatePlanRequest,
)

ACTIVE_MEMBER_STATUSES = ("APPROVED", "COMMITTED")


def _user_to_summary(user: User) -> UserSummary:
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


def _group_to_summary(group: Group) -> GroupSummary:
    """group.members must be eager-loaded (selectinload) by the caller —
    the currentSize/maleCount/femaleCount counters are maintained
    independently of this list, so without it a page could show a nonzero
    fill count with no one to back it up."""
    pw_end = None
    if group.payment_window_closes_at:
        pw_end = group.payment_window_closes_at.isoformat()
    members = None
    if group.members is not None:
        members = [
            GroupMemberSummary(
                id=m.id,
                role=m.role,
                status=m.status,
                joined_at=m.joined_at.isoformat() if m.joined_at else None,
                user=_user_to_summary(m.user),
            )
            for m in sorted(group.members, key=lambda m: m.joined_at.isoformat() if m.joined_at else "")
            if m.status in ACTIVE_MEMBER_STATUSES and m.user
        ]
    return GroupSummary(
        id=group.id,
        current_size=group.current_size,
        male_count=group.male_count,
        female_count=group.female_count,
        other_count=group.other_count,
        is_locked=group.is_locked,
        payment_window_ends_at=pw_end,
        members=members,
    )


def _offer_to_schema(offer: Offer) -> OfferInPlan | None:
    if not offer.agency:
        return None
    from app.schemas.common import AgencyPublicSummary
    agency = AgencyPublicSummary(
        id=offer.agency.id,
        name=offer.agency.name,
        slug=offer.agency.slug,
        logo_url=offer.agency.logo_url,
        description=offer.agency.description,
        verification=offer.agency.verification_status,
        city=offer.agency.city,
        state=offer.agency.state,
        avg_rating=offer.agency.avg_rating,
        total_reviews=offer.agency.review_count,
        total_trips=offer.agency.total_trips,
    )
    negotiations = [
        {"id": n.id, "round": n.round, "senderType": n.sender_type,
         "price": n.price, "message": n.message, "createdAt": n.created_at.isoformat()}
        for n in (offer.negotiations or [])
    ]
    return OfferInPlan(
        id=offer.id,
        plan_id=offer.plan_id,
        price_per_person=offer.price_per_person,
        pricing_tiers=_parse_json(offer.pricing_tiers),
        inclusions=_parse_json(offer.inclusions),
        itinerary=_parse_json(offer.itinerary),
        cancellation_policy=offer.cancellation_policy,
        cancellation_rules=_parse_json(offer.cancellation_rules),
        valid_until=offer.valid_until.isoformat() if offer.valid_until else None,
        status=offer.status,
        is_referred=offer.is_referred,
        referred_at=offer.referred_at.isoformat() if offer.referred_at else None,
        created_at=offer.created_at.isoformat(),
        updated_at=offer.updated_at.isoformat(),
        agency=agency,
        negotiations=negotiations,
    )


def _plan_to_details(plan: Plan, offers: list[Offer] | None = None) -> PlanDetails:
    group_summary = None
    if plan.group:
        group_summary = _group_to_summary(plan.group)

    offer_items = []
    selected_offer = None
    if offers:
        for o in offers:
            oi = _offer_to_schema(o)
            if oi:
                offer_items.append(oi)
                if plan.confirmed_offer_id and o.id == plan.confirmed_offer_id:
                    selected_offer = oi

    gallery = _parse_json_list(plan.gallery_urls)
    cover_image = plan.cover_image_url or ((gallery[0] if gallery else None))

    return PlanDetails(
        id=plan.id,
        slug=plan.slug,
        title=plan.title,
        destination=plan.destination,
        destination_state=plan.destination_state,
        start_date=plan.start_date.isoformat() if plan.start_date else None,
        end_date=plan.end_date.isoformat() if plan.end_date else None,
        is_date_flexible=plan.is_flexible_dates,
        budget_min=plan.budget_min,
        budget_max=plan.budget_max,
        group_size_min=plan.group_size_min,
        group_size_max=plan.group_size_max,
        vibes=_parse_json_list(plan.vibes),
        accommodation=plan.accommodation,
        group_type=plan.group_type,
        gender_pref=plan.gender_pref,
        activities=_parse_json_list(plan.activities),
        description=plan.description,
        itinerary=_parse_json(plan.itinerary),
        gallery_urls=gallery,
        cover_image_url=cover_image,
        auto_approve=plan.auto_approve,
        status=plan.status,
        plan_type=plan.plan_type,
        expires_at=plan.expires_at.isoformat() if plan.expires_at else None,
        confirmed_at=plan.confirmed_at.isoformat() if plan.confirmed_at else None,
        creator=_user_to_summary(plan.creator),
        group=group_summary,
        offers=offer_items,
        selected_offer=selected_offer,
        created_at=plan.created_at.isoformat(),
        updated_at=plan.updated_at.isoformat(),
    )


def _plan_to_meta(plan: Plan) -> PlanMeta:
    gallery = _parse_json_list(plan.gallery_urls)
    cover_image = plan.cover_image_url or ((gallery[0] if gallery else None))
    return PlanMeta(
        id=plan.id,
        slug=plan.slug,
        title=plan.title,
        destination=plan.destination,
        destination_state=plan.destination_state,
        start_date=plan.start_date.isoformat() if plan.start_date else None,
        end_date=plan.end_date.isoformat() if plan.end_date else None,
        budget_min=plan.budget_min,
        budget_max=plan.budget_max,
        group_size_min=plan.group_size_min,
        group_size_max=plan.group_size_max,
        cover_image_url=cover_image,
        gallery_urls=gallery,
        status=plan.status,
        plan_type=plan.plan_type,
        vibes=_parse_json_list(plan.vibes),
        group_type=plan.group_type,
        creator=_user_to_summary(plan.creator),
        created_at=plan.created_at.isoformat(),
    )


async def get_plan_by_slug(db: AsyncSession, slug: str) -> PlanDetails:
    result = await db.execute(
        select(Plan)
        .options(
            selectinload(Plan.creator),
            selectinload(Plan.group).selectinload(Group.members).selectinload(GroupMember.user),
            selectinload(Plan.offers).selectinload(Offer.agency),
            selectinload(Plan.offers).selectinload(Offer.negotiations),
        )
        .where(Plan.slug == slug)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise NotFoundError("Plan")
    return _plan_to_details(plan, plan.offers or [])


async def get_plan_by_id(db: AsyncSession, plan_id: str) -> PlanDetails:
    result = await db.execute(
        select(Plan)
        .options(
            selectinload(Plan.creator),
            selectinload(Plan.group).selectinload(Group.members).selectinload(GroupMember.user),
            selectinload(Plan.offers).selectinload(Offer.agency),
            selectinload(Plan.offers).selectinload(Offer.negotiations),
        )
        .where(Plan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise NotFoundError("Plan")
    return _plan_to_details(plan, plan.offers or [])


async def confirm_plan_with_offer(db: AsyncSession, plan_id: str, user_id: str, offer_id: str) -> PlanDetails:
    """Sanity-checks that the plan is actually confirmed with the given offer
    before returning it. The real confirmation (offer -> ACCEPTED, Plan ->
    CONFIRMED, Package + Group creation) happens in
    services/offers.py::accept_offer — this endpoint no longer performs a
    second, separate confirmation step."""
    plan = await db.scalar(select(Plan).where(Plan.id == plan_id))
    if not plan:
        raise NotFoundError("Plan")
    if plan.creator_id != user_id:
        raise ForbiddenError("Only the plan creator can confirm this plan")
    if plan.confirmed_offer_id != offer_id:
        raise BadRequestError("This offer is not the plan's accepted offer")
    return await get_plan_by_id(db, plan_id)


async def list_my_plans(
    db: AsyncSession, user_id: str, page: int, page_size: int
) -> tuple[list[PlanMeta], int]:
    total = await db.scalar(
        select(func.count(Plan.id)).where(Plan.creator_id == user_id)
    ) or 0
    result = await db.execute(
        select(Plan)
        .options(selectinload(Plan.creator))
        .where(Plan.creator_id == user_id)
        .order_by(Plan.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [_plan_to_meta(p) for p in result.scalars().all()], total


async def create_plan(db: AsyncSession, user_id: str, req: CreatePlanRequest) -> PlanDetails:
    slug = slugify(req.title) + "-" + str(uuid.uuid4())[:8]
    cover_image = req.cover_image_url or ((req.gallery_urls[0] if req.gallery_urls else None))
    plan = Plan(
        id=str(uuid.uuid4()),
        creator_id=user_id,
        title=req.title,
        slug=slug,
        destination=req.destination,
        destination_state=req.destination_state,
        description=req.description,
        is_flexible_dates=req.is_date_flexible,
        budget_min=req.budget_min,
        budget_max=req.budget_max,
        group_size_min=req.group_size_min,
        group_size_max=req.group_size_max,
        vibes=req.vibes or None,
        accommodation=req.accommodation,
        group_type=req.group_type,
        gender_pref=req.gender_pref,
        activities=req.activities or None,
        itinerary=[i.model_dump() for i in req.itinerary] if req.itinerary else None,
        gallery_urls=req.gallery_urls or None,
        cover_image_url=cover_image,
        auto_approve=req.auto_approve,
        plan_type=req.plan_type,
        status="DRAFT",
    )
    db.add(plan)
    await db.flush()
    await db.refresh(plan, ["creator"])
    await invalidate_pattern("plan:*")
    return _plan_to_details(plan)


async def update_plan(
    db: AsyncSession, plan_id: str, user_id: str, req: UpdatePlanRequest
) -> PlanDetails:
    result = await db.execute(
        select(Plan)
        .options(
            selectinload(Plan.creator),
            selectinload(Plan.group).selectinload(Group.members).selectinload(GroupMember.user),
        )
        .where(Plan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise NotFoundError("Plan")
    if plan.creator_id != user_id:
        raise ForbiddenError()
    if plan.status not in ("DRAFT", "OPEN"):
        raise BadRequestError("Cannot edit plan in current status")

    update_data = req.model_dump(exclude_none=True)
    json_fields = {"vibes", "activities", "gallery_urls", "itinerary"}
    gallery_updated = False
    for field, value in update_data.items():
        model_field = _plan_field_map(field)
        if field in json_fields:
            setattr(plan, model_field, value)
            if field == "gallery_urls":
                gallery_updated = True
        elif hasattr(plan, model_field):
            setattr(plan, model_field, value)

    if gallery_updated:
        gallery = _parse_json_list(plan.gallery_urls)
        plan.cover_image_url = gallery[0] if gallery else None
    elif "cover_image_url" in update_data and not update_data.get("cover_image_url"):
        gallery = _parse_json_list(plan.gallery_urls)
        plan.cover_image_url = gallery[0] if gallery else None

    await db.flush()
    await invalidate(CacheKeys.plan_detail(plan_id))
    return _plan_to_details(plan)


async def publish_plan(db: AsyncSession, plan_id: str, user_id: str) -> PlanDetails:
    result = await db.execute(
        select(Plan)
        .options(
            selectinload(Plan.creator),
            selectinload(Plan.group).selectinload(Group.members).selectinload(GroupMember.user),
        )
        .where(Plan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise NotFoundError("Plan")
    if plan.creator_id != user_id:
        raise ForbiddenError()
    if plan.status != "DRAFT":
        raise BadRequestError("Only DRAFT plans can be published")

    plan.status = "OPEN"
    plan.expires_at = datetime.now(UTC) + timedelta(days=30)
    await db.flush()
    await db.refresh(plan)
    await invalidate(CacheKeys.plan_detail(plan_id))
    await invalidate_pattern("discover:*")
    return _plan_to_details(plan)


async def cancel_plan(db: AsyncSession, plan_id: str, user_id: str) -> dict:
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise NotFoundError("Plan")
    if plan.creator_id != user_id:
        raise ForbiddenError()
    if plan.status in ("CONFIRMED", "COMPLETED"):
        raise BadRequestError("Cannot cancel a confirmed or completed plan")
    plan.status = "CANCELLED"
    await db.flush()
    await invalidate(CacheKeys.plan_detail(plan_id))
    return {"ok": True}


async def refer_plan_to_agencies(
    db: AsyncSession, plan_id: str, user_id: str, req: ReferPlanRequest
) -> dict:
    result = await db.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise NotFoundError("Plan")
    if plan.creator_id != user_id:
        raise ForbiddenError()
    await db.flush()
    return {"ok": True, "referredTo": req.agency_ids}


async def list_open_corporate_plans(
    db: AsyncSession, page: int, page_size: int
) -> tuple[list[PlanMeta], int]:
    total = await db.scalar(
        select(func.count(Plan.id))
        .where(Plan.status == "OPEN", Plan.plan_type == "CORPORATE")
    ) or 0
    result = await db.execute(
        select(Plan)
        .options(selectinload(Plan.creator))
        .where(Plan.status == "OPEN", Plan.plan_type == "CORPORATE")
        .order_by(Plan.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [_plan_to_meta(p) for p in result.scalars().all()], total


def _plan_field_map(schema_field: str) -> str:
    mapping = {
        "is_date_flexible": "is_flexible_dates",
        "destination_state": "destination_state",
        "group_size_min": "group_size_min",
        "group_size_max": "group_size_max",
        "gender_pref": "gender_pref",
        "group_type": "group_type",
        "budget_min": "budget_min",
        "budget_max": "budget_max",
        "gallery_urls": "gallery_urls",
        "cover_image_url": "cover_image_url",
        "auto_approve": "auto_approve",
    }
    return mapping.get(schema_field, schema_field)
