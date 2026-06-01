from app.schemas.base import CamelModel
from app.schemas.common import UserSummary
from app.schemas.packages import PackageDetails
from app.schemas.plans import PlanDetails


class GroupMemberResponse(CamelModel):
    id: str
    role: str
    status: str
    joined_at: str | None = None
    user: UserSummary


class GroupSummaryResponse(CamelModel):
    id: str
    plan_id: str | None = None
    package_id: str | None = None
    current_size: int
    male_count: int
    female_count: int
    other_count: int
    is_locked: bool
    payment_window_ends_at: str | None = None


class TripMembershipResponse(CamelModel):
    id: str
    status: str
    joined_at: str | None = None
    group: dict


class GroupMembersPayload(CamelModel):
    group: dict
    members: list[GroupMemberResponse]


class InviteMemberRequest(CamelModel):
    user_id: str
