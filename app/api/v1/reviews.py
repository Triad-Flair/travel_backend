from datetime import UTC, datetime
from typing import Literal
import uuid

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.agency import Agency
from app.models.group import Group, GroupMember
from app.models.offer import Offer
from app.models.package import Package
from app.models.plan import Plan
from app.models.social import Review
from app.models.user import User
from app.schemas.base import CamelModel

router = APIRouter(prefix="/reviews", tags=["reviews"])

ACTIVE_REVIEWER_STATUSES = ("APPROVED", "COMMITTED")


class CreateReviewRequest(CamelModel):
    group_id: str
    review_type: Literal["agency", "co_traveler"]
    target_agency_id: str | None = None
    target_user_id: str | None = None
    overall_rating: int = Field(..., ge=1, le=5)
    safety_rating: int = Field(..., ge=1, le=5)
    value_rating: int = Field(..., ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_targets(self):
        if self.review_type == "agency":
            if not self.target_agency_id:
                raise ValueError("Agency review must include targetAgencyId")
            if self.target_user_id:
                raise ValueError("Agency review cannot include targetUserId")
        else:
            if not self.target_user_id:
                raise ValueError("Co-traveler review must include targetUserId")
            if self.target_agency_id:
                raise ValueError("Co-traveler review cannot include targetAgencyId")
        return self


def _to_iso(value):
    return value.isoformat() if value else None


def _is_trip_over(end_date) -> bool:
    if end_date is None:
        return False
    if end_date.tzinfo is None:
        return end_date <= datetime.utcnow()
    return end_date <= datetime.now(UTC)


def _user_summary(user: User) -> dict:
    return {
        "id": user.id,
        "fullName": user.display_name or user.username or "",
        "username": user.username,
        "avatarUrl": user.avatar_url,
        "verification": user.verification_tier,
        "gender": user.gender,
        "city": user.location,
        "avgRating": user.avg_rating,
        "completedTrips": user.completed_trips,
    }


def _agency_summary(agency: Agency) -> dict:
    return {
        "id": agency.id,
        "name": agency.name,
        "slug": agency.slug,
        "logoUrl": agency.logo_url,
        "description": agency.description,
        "verification": agency.verification_status,
        "gstin": agency.gstin,
        "pan": agency.pan,
        "tourismLicense": agency.tourism_license,
        "address": agency.address,
        "phone": agency.phone,
        "email": agency.email,
        "city": agency.city,
        "state": agency.state,
        "specializations": agency.specializations,
        "destinations": agency.destinations,
        "avgRating": agency.avg_rating,
        "totalReviews": agency.review_count,
        "totalTrips": agency.total_trips,
    }


async def _resolve_group_context(db: AsyncSession, group_id: str):
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")

    plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id)) if group.plan_id else None
    package = await db.scalar(select(Package).where(Package.id == group.package_id)) if group.package_id else None

    agency = None
    if plan:
        offer = None
        if plan.confirmed_offer_id:
            offer = await db.scalar(select(Offer).where(Offer.id == plan.confirmed_offer_id))
        if not offer:
            offer = await db.scalar(
                select(Offer)
                .where(
                    Offer.plan_id == plan.id,
                    Offer.status == "ACCEPTED",
                )
                .order_by(Offer.updated_at.desc())
                .limit(1)
            )
        if offer:
            agency = await db.scalar(select(Agency).where(Agency.id == offer.agency_id))

    if not agency and package:
        agency = await db.scalar(select(Agency).where(Agency.id == package.agency_id))

    end_date = plan.end_date if plan and plan.end_date else (package.end_date if package else None)
    return group, plan, package, agency, end_date


async def _assert_member(db: AsyncSession, group_id: str, user_id: str, view_only: bool = False):
    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.status.in_(ACTIVE_REVIEWER_STATUSES),
        )
    )
    if member:
        return member
    if view_only:
        raise ForbiddenError("Only travelers from this trip can view these reviews")
    raise ForbiddenError("Only travelers from this trip can leave reviews")


def _review_record(review: Review, reviewer: User, target_agency: Agency | None, target_user: User | None) -> dict:
    return {
        "id": review.id,
        "reviewType": (review.review_type or "").lower(),
        "targetAgencyId": review.target_agency_id,
        "targetUserId": review.target_user_id,
        "overallRating": review.overall_rating,
        "safetyRating": review.safety_rating,
        "valueRating": review.value_rating,
        "comment": review.comment,
        "createdAt": _to_iso(review.created_at),
        "reviewer": _user_summary(reviewer),
        "targetAgency": _agency_summary(target_agency) if target_agency else None,
        "targetUser": _user_summary(target_user) if target_user else None,
    }


@router.get("/health")
async def health():
    return {"ok": True, "module": "reviews"}


@router.get("/groups/{group_id}/eligibility")
async def check_eligibility(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, _, _, agency, end_date = await _resolve_group_context(db, group_id)
    await _assert_member(db, group_id, current_user.user_id)

    if not _is_trip_over(end_date):
        raise BadRequestError("Reviews unlock after the trip has ended")

    existing_rows = await db.execute(
        select(Review).where(
            Review.group_id == group_id,
            Review.reviewer_id == current_user.user_id,
        )
    )
    existing_reviews = existing_rows.scalars().all()

    co_traveler_rows = await db.execute(
        select(GroupMember, User)
        .join(User, User.id == GroupMember.user_id)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.status.in_(ACTIVE_REVIEWER_STATUSES),
            GroupMember.user_id != current_user.user_id,
        )
    )

    return {
        "groupId": group_id,
        "agency": _agency_summary(agency) if agency else None,
        "coTravelers": [_user_summary(user) for _, user in co_traveler_rows.all()],
        "existingReviews": [
            {
                "id": review.id,
                "reviewType": (review.review_type or "").lower(),
                "targetAgencyId": review.target_agency_id,
                "targetUserId": review.target_user_id,
                "overallRating": review.overall_rating,
                "safetyRating": review.safety_rating,
                "valueRating": review.value_rating,
                "comment": review.comment,
                "createdAt": _to_iso(review.created_at),
            }
            for review in existing_reviews
        ],
    }


@router.get("/groups/{group_id}")
async def list_group_reviews(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, _, _, _, end_date = await _resolve_group_context(db, group_id)
    await _assert_member(db, group_id, current_user.user_id, view_only=True)
    if not _is_trip_over(end_date):
        raise BadRequestError("Reviews unlock after the trip has ended")

    rows = await db.execute(
        select(Review)
        .where(Review.group_id == group_id)
        .order_by(Review.created_at.desc())
    )
    reviews = rows.scalars().all()
    if not reviews:
        return []

    reviewer_ids = {r.reviewer_id for r in reviews if r.reviewer_id}
    agency_ids = {r.target_agency_id for r in reviews if r.target_agency_id}
    target_user_ids = {r.target_user_id for r in reviews if r.target_user_id}

    user_rows = await db.execute(select(User).where(User.id.in_(reviewer_ids | target_user_ids)))
    user_map = {user.id: user for user in user_rows.scalars().all()}

    agency_map = {}
    if agency_ids:
        agency_rows = await db.execute(select(Agency).where(Agency.id.in_(agency_ids)))
        agency_map = {agency.id: agency for agency in agency_rows.scalars().all()}

    return [
        _review_record(
            review,
            user_map[review.reviewer_id],
            agency_map.get(review.target_agency_id),
            user_map.get(review.target_user_id),
        )
        for review in reviews
        if review.reviewer_id in user_map
    ]


@router.post("", status_code=201)
async def submit_review_endpoint(
    req: CreateReviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group, _, _, agency, end_date = await _resolve_group_context(db, req.group_id)
    await _assert_member(db, req.group_id, current_user.user_id)
    if not _is_trip_over(end_date):
        raise BadRequestError("Reviews unlock after the trip has ended")

    if req.review_type == "agency":
        if not agency:
            raise BadRequestError("This trip has no agency review target")
        if req.target_agency_id and req.target_agency_id != agency.id:
            raise BadRequestError("Agency review target does not match the trip")

        duplicate = await db.scalar(
            select(Review.id).where(
                Review.reviewer_id == current_user.user_id,
                Review.group_id == req.group_id,
                Review.review_type == "agency",
                Review.target_agency_id == agency.id,
            )
        )
        if duplicate:
            raise BadRequestError("You already reviewed this agency for the trip")

        review = Review(
            id=str(uuid.uuid4()),
            reviewer_id=current_user.user_id,
            group_id=req.group_id,
            review_type="agency",
            target_agency_id=agency.id,
            target_user_id=None,
            overall_rating=req.overall_rating,
            safety_rating=req.safety_rating,
            value_rating=req.value_rating,
            comment=req.comment,
        )
        db.add(review)
        await db.flush()

        aggregate = await db.execute(
            select(func.avg(Review.overall_rating), func.count(Review.id)).where(Review.target_agency_id == agency.id)
        )
        avg_rating, count = aggregate.one()
        agency.avg_rating = float(avg_rating or 0)
        agency.review_count = int(count or 0)
    else:
        target_member = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == req.group_id,
                GroupMember.user_id == req.target_user_id,
                GroupMember.status.in_(ACTIVE_REVIEWER_STATUSES),
            )
        )
        if not target_member:
            raise BadRequestError("Co-traveler review target must belong to the same group")
        if req.target_user_id == current_user.user_id:
            raise BadRequestError("You cannot review yourself")

        duplicate = await db.scalar(
            select(Review.id).where(
                Review.reviewer_id == current_user.user_id,
                Review.group_id == req.group_id,
                Review.review_type == "co_traveler",
                Review.target_user_id == req.target_user_id,
            )
        )
        if duplicate:
            raise BadRequestError("You already reviewed this co-traveler for the trip")

        review = Review(
            id=str(uuid.uuid4()),
            reviewer_id=current_user.user_id,
            group_id=req.group_id,
            review_type="co_traveler",
            target_agency_id=None,
            target_user_id=req.target_user_id,
            overall_rating=req.overall_rating,
            safety_rating=req.safety_rating,
            value_rating=req.value_rating,
            comment=req.comment,
        )
        db.add(review)
        await db.flush()

        target_user = await db.scalar(select(User).where(User.id == req.target_user_id))
        if target_user:
            aggregate = await db.scalar(
                select(func.avg(Review.overall_rating)).where(Review.target_user_id == target_user.id)
            )
            target_user.avg_rating = float(aggregate or 0)

    reviewer = await db.scalar(select(User).where(User.id == current_user.user_id))
    target_user = await db.scalar(select(User).where(User.id == review.target_user_id)) if review.target_user_id else None
    target_agency = await db.scalar(select(Agency).where(Agency.id == review.target_agency_id)) if review.target_agency_id else None
    if not reviewer:
        raise NotFoundError("Reviewer")

    return _review_record(review, reviewer, target_agency, target_user)
