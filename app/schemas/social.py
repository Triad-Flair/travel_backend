from datetime import datetime

from app.schemas.base import CamelModel
from app.schemas.common import UserSummary


class SocialFeedAuthor(CamelModel):
    profile_type: str  # "traveler" | "agency"
    handle: str
    name: str
    avatar_url: str | None = None
    verification: str | None = None


class SocialFeedItem(CamelModel):
    id: str
    slug: str
    origin_type: str  # "plan" | "package"
    title: str
    destination: str
    destination_state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    price_low: int | None = None
    price_high: int | None = None
    group_size_min: int
    group_size_max: int
    joined_count: int
    cover_image_url: str | None = None
    excerpt: str | None = None
    created_at: str
    author: SocialFeedAuthor


class FollowStateResponse(CamelModel):
    is_following: bool
    is_own_profile: bool
    follower_count: int
    following_count: int


class FollowerEntry(CamelModel):
    id: str
    handle: str
    name: str
    avatar_url: str | None = None
    profile_type: str  # "traveler" | "agency"


class SocialTripSummary(CamelModel):
    id: str
    slug: str
    title: str
    destination: str
    destination_state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str
    cover_image_url: str | None = None
    gallery_urls: list[str] | None = None
    base_price: int | None = None


class SocialReview(CamelModel):
    id: str
    overall_rating: int
    safety_rating: int | None = None
    value_rating: int | None = None
    comment: str | None = None
    created_at: str
    reviewer: UserSummary


class TravelerProfileResponse(CamelModel):
    profile_type: str = "traveler"
    handle: str
    id: str
    name: str
    avatar_url: str | None = None
    bio: str | None = None
    travel_preferences: str | None = None
    location: str | None = None
    verification: str
    follower_count: int
    following_count: int
    avg_rating: float
    completed_trips: int
    travel_map: list[str]
    plans_created: list[SocialTripSummary]
    trips_joined: list[SocialTripSummary]
    reviews_received: list[SocialReview]


class AgencyProfileResponse(CamelModel):
    profile_type: str = "agency"
    handle: str
    id: str
    owner_id: str
    name: str
    avatar_url: str | None = None
    bio: str | None = None
    location: str | None = None
    verification: str
    follower_count: int
    following_count: int
    avg_rating: float
    total_trips: int
    total_reviews: int
    travel_map: list[str]
    packages: list[SocialTripSummary]
    reviews_received: list[SocialReview]


PublicProfileResponse = TravelerProfileResponse | AgencyProfileResponse


# Legacy review contracts still used by /reviews routes.
class ReviewResponse(CamelModel):
    id: str
    reviewer: UserSummary
    overall_rating: int
    service_rating: int | None = None
    value_rating: int | None = None
    communication_rating: int | None = None
    review_text: str | None = None
    is_verified: bool = False
    created_at: datetime


class SubmitReviewRequest(CamelModel):
    group_id: str
    overall_rating: int
    service_rating: int | None = None
    value_rating: int | None = None
    communication_rating: int | None = None
    review_text: str | None = None
