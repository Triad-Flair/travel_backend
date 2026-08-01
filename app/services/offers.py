import json
import uuid
from datetime import datetime

from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.agency import Agency, AgencyMember
from app.models.group import Group, GroupMember
from app.models.offer import Offer, OfferNegotiation
from app.models.package import Package
from app.models.plan import Plan
from app.models.user import User
from app.services import notifications as notif_svc

OPEN_OFFER_STATUSES = ("PENDING", "COUNTERED")
from app.schemas.common import AgencySummary, UserSummary
from app.schemas.offers import (
    CounterOfferRequest,
    OfferNegotiationResponse,
    OfferPlanContext,
    OfferResponse,
    PricingTier,
    RejectOfferRequest,
    SubmitOfferRequest,
)


def _parse_json(value: object):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value if isinstance(value, str) else str(value))
    except Exception:
        return None


def _to_iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _user_summary(user: User | None) -> UserSummary | None:
    if not user:
        return None
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


def _agency_summary(agency: Agency) -> AgencySummary:
    return AgencySummary(
        id=agency.id,
        name=agency.name,
        slug=agency.slug,
        logo_url=agency.logo_url,
        description=agency.description,
        verification=agency.verification_status,
        gstin=agency.gstin,
        pan=agency.pan,
        tourism_license=agency.tourism_license,
        address=agency.address,
        phone=agency.phone,
        email=agency.email,
        city=agency.city,
        state=agency.state,
        specializations=None,
        destinations=None,
        avg_rating=agency.avg_rating,
        total_reviews=agency.review_count,
        total_trips=agency.total_trips,
    )


def _negotiation_to_response(negotiation: OfferNegotiation) -> OfferNegotiationResponse:
    return OfferNegotiationResponse(
        id=negotiation.id,
        round=negotiation.round,
        sender_type=negotiation.sender_type,
        price=negotiation.price,
        inclusions_delta=_parse_json(negotiation.inclusions_delta),
        message=negotiation.message,
        created_at=negotiation.created_at.isoformat(),
    )


async def _offer_to_response(db: AsyncSession, offer: Offer) -> OfferResponse:
    agency = await db.scalar(select(Agency).where(Agency.id == offer.agency_id))
    if not agency:
        raise NotFoundError("Agency")

    plan = await db.scalar(select(Plan).where(Plan.id == offer.plan_id))
    creator = await db.scalar(select(User).where(User.id == plan.creator_id)) if plan else None

    negotiations_result = await db.execute(
        select(OfferNegotiation)
        .where(OfferNegotiation.offer_id == offer.id)
        .order_by(OfferNegotiation.created_at.asc())
    )
    negotiations = negotiations_result.scalars().all()

    plan_ctx = None
    if plan:
        plan_ctx = OfferPlanContext(
            id=plan.id,
            slug=plan.slug,
            title=plan.title,
            destination=plan.destination,
            budget_min=plan.budget_min,
            budget_max=plan.budget_max,
            start_date=_to_iso(plan.start_date),
            end_date=_to_iso(plan.end_date),
            status=plan.status,
            creator=_user_summary(creator),
        )

    pricing_tiers = _parse_json(offer.pricing_tiers)
    tiers = None
    if isinstance(pricing_tiers, list):
        tiers = []
        for item in pricing_tiers:
            if isinstance(item, dict):
                min_pax = item.get("minPax", item.get("min_pax", 0))
                price = item.get("price", 0)
                tiers.append(PricingTier(min_pax=int(min_pax), price=int(price)))

    itinerary = _parse_json(offer.itinerary)
    if not isinstance(itinerary, list):
        itinerary = None

    return OfferResponse(
        id=offer.id,
        plan_id=offer.plan_id,
        price_per_person=offer.price_per_person,
        pricing_tiers=tiers,
        inclusions=_parse_json(offer.inclusions) if isinstance(_parse_json(offer.inclusions), dict) else None,
        itinerary=itinerary,
        cancellation_policy=offer.cancellation_policy,
        cancellation_rules=_parse_json(offer.cancellation_rules) if isinstance(_parse_json(offer.cancellation_rules), dict) else None,
        valid_until=_to_iso(offer.valid_until),
        status=offer.status,
        is_referred=bool(offer.is_referred),
        referred_at=_to_iso(offer.referred_at),
        created_at=offer.created_at.isoformat(),
        updated_at=offer.updated_at.isoformat(),
        agency=_agency_summary(agency),
        negotiations=[_negotiation_to_response(neg) for neg in negotiations],
        plan=plan_ctx,
    )


async def _resolve_agency_id_for_user(db: AsyncSession, user_id: str) -> str | None:
    agency = await db.scalar(select(Agency).where(Agency.owner_id == user_id))
    if agency:
        return agency.id

    member = await db.scalar(
        select(AgencyMember).where(
            AgencyMember.user_id == user_id,
            AgencyMember.is_active == True,
        )
    )
    return member.agency_id if member else None


async def submit_offer(
    db: AsyncSession,
    agency_id: str,
    user_id: str,
    req: SubmitOfferRequest,
) -> OfferResponse:
    if not req.plan_id:
        raise BadRequestError("planId is required")

    plan = await db.scalar(select(Plan).where(Plan.id == req.plan_id))
    if not plan:
        raise NotFoundError("Plan")
    if plan.status != "OPEN":
        raise BadRequestError("Plan is not open for offers")

    existing = await db.scalar(
        select(Offer).where(
            Offer.plan_id == req.plan_id,
            Offer.agency_id == agency_id,
            Offer.status.in_(["PENDING", "COUNTERED", "ACCEPTED"]),
        )
    )
    if existing:
        raise BadRequestError("You already have an offer on this plan")

    valid_until = None
    if req.valid_until:
        try:
            valid_until = datetime.fromisoformat(req.valid_until.replace("Z", "+00:00"))
        except Exception:
            valid_until = None

    offer = Offer(
        id=str(uuid.uuid4()),
        plan_id=req.plan_id,
        agency_id=agency_id,
        price_per_person=req.price_per_person,
        pricing_tiers=[tier.model_dump(by_alias=True) for tier in (req.pricing_tiers or [])] if req.pricing_tiers else None,
        inclusions=req.inclusions or None,
        itinerary=req.itinerary or None,
        cancellation_policy=req.cancellation_policy,
        cancellation_rules=req.cancellation_rules or None,
        valid_until=valid_until,
        status="PENDING",
    )
    db.add(offer)
    await db.flush()

    if req.message:
        db.add(
            OfferNegotiation(
                id=str(uuid.uuid4()),
                offer_id=offer.id,
                round=1,
                sender_type="agency",
                price=req.price_per_person,
                message=req.message,
            )
        )
        await db.flush()

    # PRD trigger: send_bid_alert_email — notify the plan creator a new
    # offer has arrived. Previously unwired (send_offer_notification_email
    # existed in app/lib/email.py but nothing called it).
    from app.workers.tasks import send_bid_alert_email_task
    send_bid_alert_email_task.delay(offer.id)

    return await _offer_to_response(db, offer)


async def counter_offer(
    db: AsyncSession,
    offer_id: str,
    user_id: str,
    role: str,
    req: CounterOfferRequest,
) -> OfferResponse:
    offer = await db.scalar(select(Offer).where(Offer.id == offer_id))
    if not offer:
        raise NotFoundError("Offer")

    plan = await db.scalar(select(Plan).where(Plan.id == offer.plan_id))
    user_agency_id = await _resolve_agency_id_for_user(db, user_id)

    is_plan_creator = bool(plan and plan.creator_id == user_id)
    is_offer_agency = bool(user_agency_id and user_agency_id == offer.agency_id)
    if not is_plan_creator and not is_offer_agency:
        raise ForbiddenError("You are not allowed to counter this offer")

    if offer.status not in OPEN_OFFER_STATUSES:
        raise BadRequestError(f"This offer is {offer.status.lower()} and can no longer be countered")

    neg_count = await db.scalar(
        select(func.count(OfferNegotiation.id)).where(OfferNegotiation.offer_id == offer.id)
    ) or 0

    sender_type = "agency" if is_offer_agency else "user"
    negotiation = OfferNegotiation(
        id=str(uuid.uuid4()),
        offer_id=offer.id,
        round=int(neg_count) + 1,
        sender_type=sender_type,
        price=req.price,
        inclusions_delta=req.inclusions_delta or None,
        message=req.message,
    )
    db.add(negotiation)

    offer.price_per_person = req.price
    offer.status = "COUNTERED"
    await db.flush()
    # offer.updated_at has onupdate=func.now() — SQLAlchemy marks it expired
    # after this UPDATE rather than eagerly re-fetching it, and a bare
    # synchronous re-read of an expired attribute crashes under the asyncio
    # extension (MissingGreenlet). An explicit awaited refresh avoids that.
    await db.refresh(offer)

    return await _offer_to_response(db, offer)


async def accept_offer(db: AsyncSession, offer_id: str, user_id: str) -> OfferResponse:
    offer = await db.scalar(select(Offer).where(Offer.id == offer_id))
    if not offer:
        raise NotFoundError("Offer")

    plan = await db.scalar(select(Plan).where(Plan.id == offer.plan_id))
    if not plan or plan.creator_id != user_id:
        raise ForbiddenError("Only plan creator can accept")

    if offer.status not in OPEN_OFFER_STATUSES:
        raise BadRequestError(f"This offer is {offer.status.lower()} and can no longer be accepted")

    offer.status = "ACCEPTED"
    plan.confirmed_offer_id = offer.id
    plan.status = "CONFIRMED"
    plan.confirmed_at = datetime.utcnow()

    # Generate the bookable Package + Group at the agreed terms — this is the
    # only place a Plan-based booking becomes payable; nothing else in the
    # codebase creates a Group for a Plan. IDs are set here (not left to the
    # DB) so all three rows can be added and flushed together in one round
    # trip, rather than a flush per object.
    package = Package(
        id=str(uuid.uuid4()),
        agency_id=offer.agency_id,
        title=plan.title,
        slug=slugify(plan.title) + "-" + str(uuid.uuid4())[:8],
        destination=plan.destination,
        destination_state=plan.destination_state,
        start_date=plan.start_date,
        end_date=plan.end_date,
        price_per_person=offer.price_per_person,
        pricing_tiers=offer.pricing_tiers,
        group_size_min=plan.group_size_min,
        group_size_max=plan.group_size_max,
        inclusions=offer.inclusions,
        accommodation=plan.accommodation,
        vibes=plan.vibes,
        activities=plan.activities,
        cancellation_policy=offer.cancellation_policy,
        cancellation_rules=offer.cancellation_rules,
        itinerary=offer.itinerary,
        status="CONFIRMING",
        source_offer_id=offer.id,
    )
    db.add(package)

    group = Group(
        id=str(uuid.uuid4()),
        plan_id=plan.id,
        package_id=package.id,
        current_size=1,
    )
    db.add(group)

    # The plan creator is confirmed into the group (eligible to pay) but not
    # yet "COMMITTED" — that status is reserved for members with a captured
    # payment (see _finalize_capture in services/payments.py).
    db.add(
        GroupMember(
            id=str(uuid.uuid4()),
            group_id=group.id,
            user_id=user_id,
            role="CREATOR",
            status="APPROVED",
            joined_at=datetime.utcnow(),
        )
    )
    await db.flush()
    await db.refresh(offer)  # see comment in counter_offer re: expired onupdate column

    await notif_svc.create_notification(
        db, user_id, "group_chat_joined",
        "You're in the group chat!",
        f"You now have access to the group chat for {plan.title}.",
        href=f"/dashboard/messages?groupId={group.id}",
    )

    # PRD trigger: send_group_chat_verification_email
    from app.workers.tasks import send_group_chat_verification_email_task
    send_group_chat_verification_email_task.delay(user_id, group.id)

    return await _offer_to_response(db, offer)


async def reject_offer(
    db: AsyncSession,
    offer_id: str,
    user_id: str,
    req: RejectOfferRequest | None,
) -> OfferResponse:
    offer = await db.scalar(select(Offer).where(Offer.id == offer_id))
    if not offer:
        raise NotFoundError("Offer")

    plan = await db.scalar(select(Plan).where(Plan.id == offer.plan_id))
    if not plan or plan.creator_id != user_id:
        raise ForbiddenError("Only plan creator can reject")

    if offer.status not in OPEN_OFFER_STATUSES:
        raise BadRequestError(f"This offer is {offer.status.lower()} and can no longer be rejected")

    offer.status = "REJECTED"
    await db.flush()
    await db.refresh(offer)  # see comment in counter_offer re: expired onupdate column
    return await _offer_to_response(db, offer)


async def withdraw_offer(db: AsyncSession, offer_id: str, agency_id: str) -> OfferResponse:
    offer = await db.scalar(select(Offer).where(Offer.id == offer_id))
    if not offer:
        raise NotFoundError("Offer")
    if offer.agency_id != agency_id:
        raise ForbiddenError("Only the agency can withdraw this offer")

    if offer.status not in OPEN_OFFER_STATUSES:
        raise BadRequestError(f"This offer is {offer.status.lower()} and can no longer be withdrawn")

    offer.status = "WITHDRAWN"
    await db.flush()
    await db.refresh(offer)  # see comment in counter_offer re: expired onupdate column
    return await _offer_to_response(db, offer)


async def list_plan_offers(db: AsyncSession, plan_id: str, user_id: str) -> list[OfferResponse]:
    offers_result = await db.execute(
        select(Offer)
        .where(Offer.plan_id == plan_id)
        .order_by(Offer.created_at.desc())
    )
    offers = offers_result.scalars().all()
    return [await _offer_to_response(db, offer) for offer in offers]


async def list_my_offers(
    db: AsyncSession,
    agency_id: str,
    page: int,
    page_size: int,
) -> tuple[list[OfferResponse], int]:
    total = await db.scalar(select(func.count(Offer.id)).where(Offer.agency_id == agency_id)) or 0
    rows = await db.execute(
        select(Offer)
        .where(Offer.agency_id == agency_id)
        .order_by(Offer.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    offers = rows.scalars().all()
    return [await _offer_to_response(db, offer) for offer in offers], int(total)


async def list_offers_by_counterpart(
    db: AsyncSession,
    agency_id: str,
    counterpart_user_id: str,
) -> list[OfferResponse]:
    rows = await db.execute(
        select(Offer)
        .join(Plan, Plan.id == Offer.plan_id)
        .where(
            Offer.agency_id == agency_id,
            Plan.creator_id == counterpart_user_id,
        )
        .order_by(Offer.updated_at.desc())
    )
    offers = rows.scalars().all()
    return [await _offer_to_response(db, offer) for offer in offers]


async def list_offers_by_agency_owner_for_traveler(
    db: AsyncSession,
    user_id: str,
    agency_owner_user_id: str,
) -> list[OfferResponse]:
    """Traveler-side counterpart to list_offers_by_counterpart. DM conversation
    participants are plain users, so the traveler's "counterpart" in a DM with
    an agency is that agency's owner user account — resolve it to an Agency,
    then return the offers that agency has made across all of this traveler's
    plans, so the DM inbox can show the same offer cards agencies already see."""
    agency = await db.scalar(select(Agency).where(Agency.owner_id == agency_owner_user_id))
    if not agency:
        return []
    rows = await db.execute(
        select(Offer)
        .join(Plan, Plan.id == Offer.plan_id)
        .where(
            Offer.agency_id == agency.id,
            Plan.creator_id == user_id,
        )
        .order_by(Offer.updated_at.desc())
    )
    offers = rows.scalars().all()
    return [await _offer_to_response(db, offer) for offer in offers]
