import json
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import BadRequestError, NotFoundError
from app.models.agency import Agency
from app.models.group import Group, GroupMember
from app.models.offer import Offer
from app.models.package import Package
from app.models.plan import Plan
from app.models.social import Follow, Notification, ProfileView, Review
from app.models.user import User
from app.schemas.common import UserSummary
from app.schemas.social import (
    AgencyProfileResponse,
    FollowStateResponse,
    FollowerEntry,
    PublicProfileResponse,
    ReviewResponse,
    SocialFeedAuthor,
    SocialFeedItem,
    SocialReview,
    SocialTripSummary,
    SubmitReviewRequest,
    TravelerProfileResponse,
)

ACTIVE_FEED_STATUSES = {"OPEN", "CONFIRMING", "CONFIRMED"}


def _json_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [str(item) for item in raw if item is not None and str(item).strip()]
    if isinstance(raw, bytes):
        raw = raw.decode(errors="ignore")
    if isinstance(raw, str):
        if not raw.strip():
            return []
        if not raw.strip().startswith("["):
            return [part.strip() for part in raw.split(",") if part.strip()]
    try:
        data = json.loads(str(raw))
        if isinstance(data, list):
            return [str(item) for item in data if item is not None]
    except Exception:
        return []
    return []


def _dedupe(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _follow_target_is(target: str):
    # Cast keeps comparisons compatible even when Postgres enum/text typing
    # comes back differently across environments/drivers.
    return cast(Follow.target_type, String) == target


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


def _trip_summary_from_plan(plan: Plan) -> SocialTripSummary:
    gallery = _json_list(plan.gallery_urls)
    cover_image = plan.cover_image_url or (gallery[0] if gallery else None)
    return SocialTripSummary(
        id=plan.id,
        slug=plan.slug,
        title=plan.title,
        destination=plan.destination,
        destination_state=plan.destination_state,
        start_date=plan.start_date.isoformat() if plan.start_date else None,
        end_date=plan.end_date.isoformat() if plan.end_date else None,
        status=plan.status,
        cover_image_url=cover_image,
        gallery_urls=gallery,
        base_price=plan.budget_min,
    )


def _trip_summary_from_package(pkg: Package) -> SocialTripSummary:
    gallery = _json_list(pkg.gallery_urls)
    cover_image = pkg.cover_image_url or (gallery[0] if gallery else None)
    return SocialTripSummary(
        id=pkg.id,
        slug=pkg.slug,
        title=pkg.title,
        destination=pkg.destination,
        destination_state=pkg.destination_state,
        start_date=pkg.start_date.isoformat() if pkg.start_date else None,
        end_date=pkg.end_date.isoformat() if pkg.end_date else None,
        status=pkg.status,
        cover_image_url=cover_image,
        gallery_urls=gallery,
        base_price=pkg.price_per_person,
    )


async def _resolve_profile(db: AsyncSession, handle: str) -> tuple[str, User | Agency]:
    user = await db.scalar(select(User).where(User.username == handle))
    if user:
        return "traveler", user

    agency = await db.scalar(select(Agency).where(Agency.slug == handle))
    if agency:
        return "agency", agency

    raise NotFoundError("Profile")


async def _joined_counts_for_plans(db: AsyncSession, plan_ids: list[str]) -> dict[str, int]:
    if not plan_ids:
        return {}

    rows = await db.execute(
        select(Group.plan_id, Group.current_size)
        .where(Group.plan_id.in_(plan_ids))
    )
    return {plan_id: int(current_size or 0) for plan_id, current_size in rows.all() if plan_id}


async def _joined_counts_for_packages(db: AsyncSession, package_ids: list[str]) -> dict[str, int]:
    if not package_ids:
        return {}

    rows = await db.execute(
        select(Group.package_id, Group.current_size)
        .where(Group.package_id.in_(package_ids))
    )
    return {pkg_id: int(current_size or 0) for pkg_id, current_size in rows.all() if pkg_id}


def _plan_feed_item(plan: Plan, creator: User, joined_count: int) -> SocialFeedItem:
    gallery = _json_list(plan.gallery_urls)
    cover_image = plan.cover_image_url or (gallery[0] if gallery else None)
    return SocialFeedItem(
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
        group_size_min=plan.group_size_min,
        group_size_max=plan.group_size_max,
        joined_count=joined_count,
        cover_image_url=cover_image,
        excerpt=plan.description,
        created_at=plan.created_at.isoformat(),
        author=SocialFeedAuthor(
            profile_type="traveler",
            handle=creator.username or creator.id,
            name=creator.display_name or creator.username or "Traveler",
            avatar_url=creator.avatar_url,
            verification=creator.verification_tier,
        ),
    )


def _package_feed_item(pkg: Package, agency: Agency, joined_count: int) -> SocialFeedItem:
    gallery = _json_list(pkg.gallery_urls)
    cover_image = pkg.cover_image_url or (gallery[0] if gallery else None)
    return SocialFeedItem(
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
        group_size_min=pkg.group_size_min,
        group_size_max=pkg.group_size_max,
        joined_count=joined_count,
        cover_image_url=cover_image,
        excerpt=None,
        created_at=pkg.created_at.isoformat(),
        author=SocialFeedAuthor(
            profile_type="agency",
            handle=agency.slug,
            name=agency.name,
            avatar_url=agency.logo_url,
            verification=agency.verification_status,
        ),
    )


async def _build_feed(
    db: AsyncSession,
    *,
    limit: int,
    plan_creator_ids: list[str] | None = None,
    agency_ids: list[str] | None = None,
) -> list[SocialFeedItem]:
    plan_query = (
        select(Plan, User)
        .join(User, User.id == Plan.creator_id)
        .where(Plan.status.in_(ACTIVE_FEED_STATUSES))
        .order_by(Plan.created_at.desc())
        .limit(limit)
    )
    if plan_creator_ids is not None:
        if not plan_creator_ids:
            plan_rows: list[tuple[Plan, User]] = []
        else:
            plan_query = plan_query.where(Plan.creator_id.in_(plan_creator_ids))
            result = await db.execute(plan_query)
            plan_rows = result.all()
    else:
        result = await db.execute(plan_query)
        plan_rows = result.all()

    package_query = (
        select(Package, Agency)
        .join(Agency, Agency.id == Package.agency_id)
        .where(Package.status.in_(ACTIVE_FEED_STATUSES))
        .order_by(Package.created_at.desc())
        .limit(limit)
    )
    if agency_ids is not None:
        if not agency_ids:
            package_rows: list[tuple[Package, Agency]] = []
        else:
            package_query = package_query.where(Package.agency_id.in_(agency_ids))
            result = await db.execute(package_query)
            package_rows = result.all()
    else:
        result = await db.execute(package_query)
        package_rows = result.all()

    plan_ids = [plan.id for plan, _ in plan_rows]
    package_ids = [pkg.id for pkg, _ in package_rows]
    plan_joined = await _joined_counts_for_plans(db, plan_ids)
    package_joined = await _joined_counts_for_packages(db, package_ids)

    items: list[SocialFeedItem] = []
    for plan, creator in plan_rows:
        items.append(_plan_feed_item(plan, creator, plan_joined.get(plan.id, 0)))
    for pkg, agency in package_rows:
        items.append(_package_feed_item(pkg, agency, package_joined.get(pkg.id, 0)))

    items.sort(key=lambda item: item.created_at, reverse=True)
    return items[:limit]


async def get_feed(db: AsyncSession, limit: int = 20) -> list[SocialFeedItem]:
    return await _build_feed(db, limit=max(1, min(limit, 50)))


async def get_following_feed(db: AsyncSession, user_id: str, limit: int = 20) -> list[SocialFeedItem]:
    follows = await db.execute(
        select(Follow).where(Follow.follower_user_id == user_id)
    )
    rows = follows.scalars().all()

    plan_creator_ids = [f.target_user_id for f in rows if f.target_type == "USER" and f.target_user_id]
    agency_ids = [f.target_agency_id for f in rows if f.target_type == "AGENCY" and f.target_agency_id]
    if not plan_creator_ids and not agency_ids:
        return []

    return await _build_feed(
        db,
        limit=max(1, min(limit, 50)),
        plan_creator_ids=plan_creator_ids,
        agency_ids=agency_ids,
    )


async def get_public_profile(db: AsyncSession, handle: str) -> PublicProfileResponse:
    kind, profile = await _resolve_profile(db, handle)

    if kind == "traveler":
        user = profile
        follower_count = await db.scalar(
            select(func.count(Follow.id)).where(
                _follow_target_is("USER"),
                Follow.target_user_id == user.id,
            )
        ) or 0
        following_count = await db.scalar(
            select(func.count(Follow.id)).where(Follow.follower_user_id == user.id)
        ) or 0

        plans_result = await db.execute(
            select(Plan)
            .where(
                Plan.creator_id == user.id,
                Plan.status.in_(ACTIVE_FEED_STATUSES),
            )
            .order_by(Plan.created_at.desc())
            .limit(12)
        )
        plans_created = plans_result.scalars().all()

        member_groups = await db.execute(
            select(GroupMember.group_id)
            .where(
                GroupMember.user_id == user.id,
                GroupMember.status.in_(["APPROVED", "COMMITTED"]),
            )
            .order_by(GroupMember.joined_at.desc())
            .limit(24)
        )
        group_ids = [gid for (gid,) in member_groups.all() if gid]
        trips_joined: list[SocialTripSummary] = []
        if group_ids:
            groups_result = await db.execute(
                select(Group)
                .where(Group.id.in_(group_ids))
            )
            groups = groups_result.scalars().all()

            plan_ids = [g.plan_id for g in groups if g.plan_id]
            pkg_ids = [g.package_id for g in groups if g.package_id]

            plan_map: dict[str, Plan] = {}
            if plan_ids:
                r = await db.execute(select(Plan).where(Plan.id.in_(plan_ids)))
                plan_map = {p.id: p for p in r.scalars().all()}

            pkg_map: dict[str, Package] = {}
            if pkg_ids:
                r = await db.execute(select(Package).where(Package.id.in_(pkg_ids)))
                pkg_map = {p.id: p for p in r.scalars().all()}

            for group in groups:
                if group.plan_id and group.plan_id in plan_map:
                    trip = _trip_summary_from_plan(plan_map[group.plan_id])
                    trips_joined.append(trip)
                elif group.package_id and group.package_id in pkg_map:
                    trip = _trip_summary_from_package(pkg_map[group.package_id])
                    trips_joined.append(trip)

        review_rows = await db.execute(
            select(Review, User)
            .join(User, User.id == Review.reviewer_id)
            .where(
                Review.target_user_id == user.id,
                Review.review_type == "co_traveler",
            )
            .order_by(Review.created_at.desc())
            .limit(12)
        )
        reviews_received = [
            SocialReview(
                id=review.id,
                overall_rating=review.overall_rating,
                safety_rating=review.safety_rating,
                value_rating=review.value_rating,
                comment=review.comment,
                created_at=review.created_at.isoformat(),
                reviewer=_user_summary(reviewer),
            )
            for review, reviewer in review_rows.all()
        ]

        travel_map = _dedupe(
            [*map(lambda p: p.destination, plans_created), *map(lambda t: t.destination, trips_joined)]
        )

        return TravelerProfileResponse(
            handle=user.username or user.id,
            id=user.id,
            name=user.display_name or user.username or "Traveler",
            avatar_url=user.avatar_url,
            bio=user.bio,
            travel_preferences=user.travel_style,
            location=user.location,
            verification=user.verification_tier,
            follower_count=int(follower_count),
            following_count=int(following_count),
            avg_rating=float(user.avg_rating or 0),
            completed_trips=int(user.completed_trips or 0),
            travel_map=travel_map,
            plans_created=[_trip_summary_from_plan(plan) for plan in plans_created],
            trips_joined=trips_joined,
            reviews_received=reviews_received,
        )

    agency = profile
    follower_count = await db.scalar(
        select(func.count(Follow.id)).where(
            _follow_target_is("AGENCY"),
            Follow.target_agency_id == agency.id,
        )
    ) or 0
    following_count = await db.scalar(
        select(func.count(Follow.id)).where(Follow.follower_user_id == agency.owner_id)
    ) or 0

    packages_result = await db.execute(
        select(Package)
        .where(
            Package.agency_id == agency.id,
            Package.status.in_(ACTIVE_FEED_STATUSES),
        )
        .order_by(Package.created_at.desc())
        .limit(12)
    )
    packages = packages_result.scalars().all()

    review_rows = await db.execute(
        select(Review, User)
        .join(User, User.id == Review.reviewer_id)
        .where(Review.target_agency_id == agency.id)
        .order_by(Review.created_at.desc())
        .limit(12)
    )
    reviews_received = [
        SocialReview(
            id=review.id,
            overall_rating=review.overall_rating,
            safety_rating=review.safety_rating,
            value_rating=review.value_rating,
            comment=review.comment,
            created_at=review.created_at.isoformat(),
            reviewer=_user_summary(reviewer),
        )
        for review, reviewer in review_rows.all()
    ]

    travel_map = _dedupe([
        *_json_list(agency.destinations),
        *map(lambda p: p.destination, packages),
    ])

    location = ", ".join([part for part in [agency.city, agency.state] if part])

    return AgencyProfileResponse(
        handle=agency.slug,
        id=agency.id,
        owner_id=agency.owner_id,
        name=agency.name,
        avatar_url=agency.logo_url,
        bio=agency.description,
        location=location or None,
        verification=agency.verification_status,
        follower_count=int(follower_count),
        following_count=int(following_count),
        avg_rating=float(agency.avg_rating or 0),
        total_trips=int(agency.total_trips or 0),
        total_reviews=int(agency.review_count or 0),
        travel_map=travel_map,
        packages=[_trip_summary_from_package(pkg) for pkg in packages],
        reviews_received=reviews_received,
    )


async def get_follow_state(db: AsyncSession, handle: str, current_user_id: str) -> FollowStateResponse:
    kind, profile = await _resolve_profile(db, handle)

    if kind == "traveler":
        user = profile
        is_own_profile = user.id == current_user_id
        is_following = False
        if not is_own_profile:
            is_following = bool(
                await db.scalar(
                    select(Follow.id).where(
                        Follow.follower_user_id == current_user_id,
                        _follow_target_is("USER"),
                        Follow.target_user_id == user.id,
                    )
                )
            )

        follower_count = await db.scalar(
            select(func.count(Follow.id)).where(
                _follow_target_is("USER"),
                Follow.target_user_id == user.id,
            )
        ) or 0
        following_count = await db.scalar(
            select(func.count(Follow.id)).where(Follow.follower_user_id == user.id)
        ) or 0

        return FollowStateResponse(
            is_following=is_following,
            is_own_profile=is_own_profile,
            follower_count=int(follower_count),
            following_count=int(following_count),
        )

    agency = profile
    is_own_profile = agency.owner_id == current_user_id
    is_following = False
    if not is_own_profile:
        is_following = bool(
                await db.scalar(
                    select(Follow.id).where(
                        Follow.follower_user_id == current_user_id,
                        _follow_target_is("AGENCY"),
                        Follow.target_agency_id == agency.id,
                    )
                )
        )

    follower_count = await db.scalar(
        select(func.count(Follow.id)).where(
            _follow_target_is("AGENCY"),
            Follow.target_agency_id == agency.id,
        )
    ) or 0
    following_count = await db.scalar(
        select(func.count(Follow.id)).where(Follow.follower_user_id == agency.owner_id)
    ) or 0

    return FollowStateResponse(
        is_following=is_following,
        is_own_profile=is_own_profile,
        follower_count=int(follower_count),
        following_count=int(following_count),
    )


async def follow_user(db: AsyncSession, handle: str, current_user_id: str) -> FollowStateResponse:
    kind, profile = await _resolve_profile(db, handle)

    if kind == "traveler":
        user = profile
        if user.id == current_user_id:
            raise BadRequestError("You cannot follow your own profile")

        existing = await db.scalar(
            select(Follow.id).where(
                Follow.follower_user_id == current_user_id,
                _follow_target_is("USER"),
                Follow.target_user_id == user.id,
            )
        )
        if not existing:
            db.add(
                Follow(
                    id=str(uuid.uuid4()),
                    follower_user_id=current_user_id,
                    target_type="USER",
                    target_user_id=user.id,
                )
            )
            db.add(
                Notification(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    type="profile_followed",
                    title="New follower",
                    body="Someone started following you",
                )
            )
    else:
        agency = profile
        if agency.owner_id == current_user_id:
            raise BadRequestError("You cannot follow your own profile")

        existing = await db.scalar(
            select(Follow.id).where(
                Follow.follower_user_id == current_user_id,
                _follow_target_is("AGENCY"),
                Follow.target_agency_id == agency.id,
            )
        )
        if not existing:
            db.add(
                Follow(
                    id=str(uuid.uuid4()),
                    follower_user_id=current_user_id,
                    target_type="AGENCY",
                    target_agency_id=agency.id,
                )
            )
            db.add(
                Notification(
                    id=str(uuid.uuid4()),
                    user_id=agency.owner_id,
                    type="profile_followed",
                    title="New follower",
                    body=f"{agency.name} got a new follower",
                )
            )

    await db.flush()
    return await get_follow_state(db, handle, current_user_id)


async def unfollow_user(db: AsyncSession, handle: str, current_user_id: str) -> FollowStateResponse:
    kind, profile = await _resolve_profile(db, handle)

    if kind == "traveler":
        user = profile
        result = await db.execute(
            select(Follow).where(
                Follow.follower_user_id == current_user_id,
                _follow_target_is("USER"),
                Follow.target_user_id == user.id,
            )
        )
        for follow in result.scalars().all():
            await db.delete(follow)
    else:
        agency = profile
        result = await db.execute(
            select(Follow).where(
                Follow.follower_user_id == current_user_id,
                _follow_target_is("AGENCY"),
                Follow.target_agency_id == agency.id,
            )
        )
        for follow in result.scalars().all():
            await db.delete(follow)

    await db.flush()
    return await get_follow_state(db, handle, current_user_id)


async def record_profile_view(db: AsyncSession, handle: str, viewer_user_id: str | None) -> dict:
    if not viewer_user_id:
        return {"recorded": False}

    kind, profile = await _resolve_profile(db, handle)
    if kind == "traveler":
        user = profile
        if user.id == viewer_user_id:
            return {"recorded": False}

        db.add(
            ProfileView(
                id=str(uuid.uuid4()),
                viewer_user_id=viewer_user_id,
                target_owner_user_id=user.id,
                target_type="USER",
                target_user_id=user.id,
            )
        )
        await db.flush()
        return {"recorded": True}

    agency = profile
    if agency.owner_id == viewer_user_id:
        return {"recorded": False}

    db.add(
        ProfileView(
            id=str(uuid.uuid4()),
            viewer_user_id=viewer_user_id,
            target_owner_user_id=agency.owner_id,
            target_type="AGENCY",
            target_agency_id=agency.id,
        )
    )
    await db.flush()
    return {"recorded": True}


async def get_followers(db: AsyncSession, handle: str, page: int, page_size: int) -> list[FollowerEntry]:
    kind, profile = await _resolve_profile(db, handle)

    where = [_follow_target_is("USER"), Follow.target_user_id == profile.id] if kind == "traveler" else [
        _follow_target_is("AGENCY"),
        Follow.target_agency_id == profile.id,
    ]

    follow_rows = await db.execute(
        select(Follow.follower_user_id)
        .where(*where)
        .order_by(Follow.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    follower_ids = [user_id for (user_id,) in follow_rows.all() if user_id]
    if not follower_ids:
        return []

    users_result = await db.execute(select(User).where(User.id.in_(follower_ids)))
    users = {user.id: user for user in users_result.scalars().all()}

    return [
        FollowerEntry(
            id=users[user_id].id,
            handle=users[user_id].username or users[user_id].id,
            name=users[user_id].display_name or users[user_id].username or "Traveler",
            avatar_url=users[user_id].avatar_url,
            profile_type="traveler",
        )
        for user_id in follower_ids
        if user_id in users
    ]


async def get_following(db: AsyncSession, handle: str, page: int, page_size: int) -> list[FollowerEntry]:
    kind, profile = await _resolve_profile(db, handle)
    follower_user_id = profile.id if kind == "traveler" else profile.owner_id

    follow_rows = await db.execute(
        select(Follow)
        .where(Follow.follower_user_id == follower_user_id)
        .order_by(Follow.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    follows = follow_rows.scalars().all()
    if not follows:
        return []

    user_ids = [f.target_user_id for f in follows if f.target_type == "USER" and f.target_user_id]
    agency_ids = [f.target_agency_id for f in follows if f.target_type == "AGENCY" and f.target_agency_id]

    users: dict[str, User] = {}
    agencies: dict[str, Agency] = {}
    if user_ids:
        rows = await db.execute(select(User).where(User.id.in_(user_ids)))
        users = {u.id: u for u in rows.scalars().all()}
    if agency_ids:
        rows = await db.execute(select(Agency).where(Agency.id.in_(agency_ids)))
        agencies = {a.id: a for a in rows.scalars().all()}

    output: list[FollowerEntry] = []
    for follow in follows:
        if follow.target_type == "USER" and follow.target_user_id and follow.target_user_id in users:
            user = users[follow.target_user_id]
            output.append(
                FollowerEntry(
                    id=user.id,
                    handle=user.username or user.id,
                    name=user.display_name or user.username or "Traveler",
                    avatar_url=user.avatar_url,
                    profile_type="traveler",
                )
            )
        elif follow.target_type == "AGENCY" and follow.target_agency_id and follow.target_agency_id in agencies:
            agency = agencies[follow.target_agency_id]
            output.append(
                FollowerEntry(
                    id=agency.id,
                    handle=agency.slug,
                    name=agency.name,
                    avatar_url=agency.logo_url,
                    profile_type="agency",
                )
            )

    return output


async def submit_review(db: AsyncSession, user_id: str, req: SubmitReviewRequest) -> ReviewResponse:
    """Not currently reachable from any router — the live review endpoint is
    POST /reviews in app/api/v1/reviews.py (submit_review_endpoint), which
    has its own inline Review-creation logic. send_review_alert_email is
    wired there, not here."""
    group = await db.scalar(select(Group).where(Group.id == req.group_id))
    if not group:
        raise NotFoundError("Group")

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == req.group_id,
            GroupMember.user_id == user_id,
            GroupMember.status.in_(["APPROVED", "COMMITTED"]),
        )
    )
    if not member:
        raise BadRequestError("You were not part of this trip")

    existing = await db.scalar(
        select(Review).where(
            Review.group_id == req.group_id,
            Review.reviewer_id == user_id,
        )
    )
    if existing:
        raise BadRequestError("You have already reviewed this trip")

    target_user_id = None
    target_agency_id = None
    review_type = "co_traveler"

    if group.package_id:
        pkg = await db.scalar(select(Package).where(Package.id == group.package_id))
        if pkg:
            target_agency_id = pkg.agency_id
            review_type = "agency"
    elif group.plan_id:
        plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id))
        if plan:
            target_user_id = plan.creator_id
            review_type = "co_traveler"
            if plan.confirmed_offer_id:
                offer = await db.scalar(select(Offer).where(Offer.id == plan.confirmed_offer_id))
                if offer:
                    target_agency_id = offer.agency_id

    review = Review(
        id=str(uuid.uuid4()),
        reviewer_id=user_id,
        review_type=review_type,
        target_agency_id=target_agency_id,
        target_user_id=target_user_id,
        group_id=req.group_id,
        overall_rating=req.overall_rating,
        safety_rating=req.service_rating or req.overall_rating,
        value_rating=req.value_rating or req.overall_rating,
        comment=req.review_text,
    )
    db.add(review)
    await db.flush()

    reviewer = await db.get(User, user_id)
    return ReviewResponse(
        id=review.id,
        reviewer=_user_summary(reviewer),
        overall_rating=review.overall_rating,
        service_rating=review.safety_rating,
        value_rating=review.value_rating,
        communication_rating=review.safety_rating,
        review_text=review.comment,
        is_verified=False,
        created_at=review.created_at,
    )
