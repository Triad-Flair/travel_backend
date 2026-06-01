import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.group import Group, GroupMember
from app.models.package import Package
from app.models.plan import Plan
from app.models.user import User
from app.schemas.offers import OfferResponse, SubmitOfferRequest
from app.schemas.groups import GroupMemberResponse, GroupMembersPayload, InviteMemberRequest, TripMembershipResponse
from app.schemas.common import UserSummary
from app.services import offers as offer_svc

router = APIRouter(prefix="/groups", tags=["groups"])

ACTIVE_MEMBER_STATUSES = {"INTERESTED", "APPROVED", "COMMITTED"}


def _iso(value):
    return value.isoformat() if value else None


def _user_summary(user: User) -> UserSummary:
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


def _plan_payload(plan: Plan | None) -> dict | None:
    if not plan:
        return None
    return {
        "id": plan.id,
        "slug": plan.slug,
        "title": plan.title,
        "destination": plan.destination,
        "startDate": _iso(plan.start_date),
        "endDate": _iso(plan.end_date),
        "status": plan.status,
        "coverImageUrl": plan.cover_image_url,
    }


def _package_payload(package: Package | None) -> dict | None:
    if not package:
        return None
    return {
        "id": package.id,
        "slug": package.slug,
        "title": package.title,
        "destination": package.destination,
        "startDate": _iso(package.start_date),
        "endDate": _iso(package.end_date),
        "status": package.status,
        "galleryUrls": package.gallery_urls,
        "basePrice": package.price_per_person,
    }


async def _group_payload(db: AsyncSession, group: Group) -> dict:
    plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id)) if group.plan_id else None
    package = await db.scalar(select(Package).where(Package.id == group.package_id)) if group.package_id else None

    return {
        "id": group.id,
        "planId": group.plan_id,
        "packageId": group.package_id,
        "currentSize": group.current_size,
        "maleCount": group.male_count,
        "femaleCount": group.female_count,
        "otherCount": group.other_count,
        "isLocked": group.is_locked,
        "paymentWindowEndsAt": _iso(group.payment_window_closes_at),
        "plan": _plan_payload(plan),
        "package": _package_payload(package),
    }


async def _assert_creator(db: AsyncSession, group_id: str, user_id: str) -> GroupMember:
    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    if not member or member.role != "CREATOR":
        raise ForbiddenError("Only the group creator can perform this action")
    return member


async def _update_group_size(db: AsyncSession, group_id: str, delta: int):
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")
    next_size = max(0, (group.current_size or 0) + delta)
    group.current_size = next_size


@router.get("/my", response_model=list[TripMembershipResponse])
async def list_my_groups(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.execute(
        select(GroupMember)
        .where(
            GroupMember.user_id == current_user.user_id,
            GroupMember.status.in_(ACTIVE_MEMBER_STATUSES),
        )
        .order_by(GroupMember.joined_at.desc())
    )
    memberships = rows.scalars().all()

    groups: dict[str, Group] = {}
    if memberships:
        group_ids = [m.group_id for m in memberships]
        group_rows = await db.execute(select(Group).where(Group.id.in_(group_ids)))
        groups = {group.id: group for group in group_rows.scalars().all()}

    payload: list[TripMembershipResponse] = []
    for membership in memberships:
        group = groups.get(membership.group_id)
        if not group:
            continue
        payload.append(
            TripMembershipResponse(
                id=membership.id,
                status=membership.status,
                joined_at=_iso(membership.joined_at),
                group=await _group_payload(db, group),
            )
        )

    return payload


@router.get("/{group_id}/members", response_model=GroupMembersPayload)
async def get_members(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")

    member_rows = await db.execute(
        select(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.status.in_(ACTIVE_MEMBER_STATUSES),
        )
        .order_by(GroupMember.joined_at.asc())
    )
    members = member_rows.scalars().all()
    user_ids = [member.user_id for member in members]

    users: dict[str, User] = {}
    if user_ids:
        rows = await db.execute(select(User).where(User.id.in_(user_ids)))
        users = {user.id: user for user in rows.scalars().all()}

    payload_members: list[GroupMemberResponse] = []
    for member in members:
        user = users.get(member.user_id)
        if not user:
            continue
        payload_members.append(
            GroupMemberResponse(
                id=member.id,
                role=member.role,
                status=member.status,
                joined_at=_iso(member.joined_at),
                user=_user_summary(user),
            )
        )

    return GroupMembersPayload(
        group=await _group_payload(db, group),
        members=payload_members,
    )


@router.post("/{group_id}/join")
async def join_group(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")
    if group.is_locked:
        raise BadRequestError("Group is locked")

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == current_user.user_id,
        )
    )

    if member and member.status in ACTIVE_MEMBER_STATUSES:
        user = await db.get(User, member.user_id)
        return GroupMemberResponse(
            id=member.id,
            role=member.role,
            status=member.status,
            joined_at=_iso(member.joined_at),
            user=_user_summary(user),
        )

    if member:
        member.status = "INTERESTED"
        member.joined_at = datetime.now(UTC)
        member.left_at = None
    else:
        member = GroupMember(
            id=str(uuid.uuid4()),
            group_id=group_id,
            user_id=current_user.user_id,
            role="MEMBER",
            status="INTERESTED",
            joined_at=datetime.now(UTC),
        )
        db.add(member)

    await _update_group_size(db, group_id, 1)
    await db.flush()

    user = await db.get(User, member.user_id)
    return GroupMemberResponse(
        id=member.id,
        role=member.role,
        status=member.status,
        joined_at=_iso(member.joined_at),
        user=_user_summary(user),
    )


@router.post("/{group_id}/offers", response_model=OfferResponse, status_code=201)
async def submit_offer_for_group(
    group_id: str,
    req: SubmitOfferRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agency_id = current_user.require_agency()
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")
    if not group.plan_id:
        raise BadRequestError("Offers can only be submitted for plan-based groups")

    payload = req.model_copy(update={"plan_id": group.plan_id})
    return await offer_svc.submit_offer(db, agency_id, current_user.user_id, payload)


@router.post("/{group_id}/leave")
async def leave_group(
    group_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == current_user.user_id,
        )
    )
    if not member:
        raise NotFoundError("Member")
    if member.role == "CREATOR":
        raise BadRequestError("Creator cannot leave the group")
    if member.status in {"LEFT", "REMOVED"}:
        return {"success": True}

    member.status = "LEFT"
    member.left_at = datetime.now(UTC)
    await _update_group_size(db, group_id, -1)
    await db.flush()
    return {"success": True}


@router.post("/{group_id}/approve/{user_id}")
async def approve_member(
    group_id: str,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_creator(db, group_id, current_user.user_id)

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    if not member:
        raise NotFoundError("Member")

    member.status = "APPROVED"
    if not member.joined_at:
        member.joined_at = datetime.now(UTC)
    await db.flush()

    user = await db.get(User, member.user_id)
    return GroupMemberResponse(
        id=member.id,
        role=member.role,
        status=member.status,
        joined_at=_iso(member.joined_at),
        user=_user_summary(user),
    )


@router.post("/{group_id}/remove/{user_id}")
async def remove_member(
    group_id: str,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_creator(db, group_id, current_user.user_id)

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
    )
    if not member:
        raise NotFoundError("Member")
    if member.role == "CREATOR":
        raise BadRequestError("Cannot remove group creator")
    if member.status in {"LEFT", "REMOVED"}:
        return {"success": True}

    member.status = "REMOVED"
    member.left_at = datetime.now(UTC)
    await _update_group_size(db, group_id, -1)
    await db.flush()
    return {"success": True}


@router.post("/{group_id}/invite")
async def invite_member(
    group_id: str,
    req: InviteMemberRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_creator(db, group_id, current_user.user_id)

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == req.user_id,
        )
    )

    if member and member.status in ACTIVE_MEMBER_STATUSES:
        raise BadRequestError("User is already in the group")

    if member:
        member.status = "APPROVED"
        member.joined_at = datetime.now(UTC)
        member.left_at = None
    else:
        member = GroupMember(
            id=str(uuid.uuid4()),
            group_id=group_id,
            user_id=req.user_id,
            role="MEMBER",
            status="APPROVED",
            joined_at=datetime.now(UTC),
        )
        db.add(member)

    await _update_group_size(db, group_id, 1)
    await db.flush()

    user = await db.get(User, member.user_id)
    return GroupMemberResponse(
        id=member.id,
        role=member.role,
        status=member.status,
        joined_at=_iso(member.joined_at),
        user=_user_summary(user),
    )
