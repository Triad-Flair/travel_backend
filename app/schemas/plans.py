from datetime import datetime

from pydantic import Field

from app.schemas.base import CamelModel
from app.schemas.common import AgencyPublicSummary, UserSummary


class ItineraryItem(CamelModel):
    day: int
    title: str
    description: str | None = None
    highlights: list[str] | None = None
    meals: list[str] | None = None
    accommodation: str | None = None
    transport: str | None = None
    images: list[str] | None = None


class Negotiation(CamelModel):
    id: str
    round: int
    sender_type: str  # "user" | "agency"
    price: int | None = None
    inclusions_delta: dict | None = None
    message: str | None = None
    created_at: datetime


class OfferInPlan(CamelModel):
    id: str
    plan_id: str | None = None
    price_per_person: int
    pricing_tiers: list[dict] | None = None
    inclusions: dict | None = None
    itinerary: list[ItineraryItem] | None = None
    cancellation_policy: str | None = None
    cancellation_rules: dict | None = None
    valid_until: str | None = None
    status: str
    is_referred: bool = False
    referred_at: str | None = None
    created_at: str
    updated_at: str
    agency: AgencyPublicSummary
    negotiations: list[Negotiation] = []


class GroupSummary(CamelModel):
    id: str
    current_size: int
    male_count: int = 0
    female_count: int = 0
    other_count: int = 0
    is_locked: bool
    payment_window_ends_at: str | None = None


# Matches frontend PlanDetails exactly
class PlanDetails(CamelModel):
    id: str
    slug: str
    title: str
    destination: str
    destination_state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_date_flexible: bool = False
    budget_min: int | None = None
    budget_max: int | None = None
    group_size_min: int
    group_size_max: int
    vibes: list[str] | None = None
    accommodation: str | None = None
    group_type: str | None = None
    gender_pref: str | None = None
    age_range_min: int | None = None
    age_range_max: int | None = None
    activities: list[str] | None = None
    description: str | None = None
    itinerary: list[ItineraryItem] | None = None
    gallery_urls: list[str] | None = None
    cover_image_url: str | None = None
    auto_approve: bool = False
    status: str
    plan_type: str = "STANDARD"
    expires_at: str | None = None
    confirmed_at: str | None = None
    creator: UserSummary
    group: GroupSummary | None = None
    offers: list[OfferInPlan] = []
    selected_offer: OfferInPlan | None = None
    created_at: str
    updated_at: str


# Lightweight for discover/list
class PlanMeta(CamelModel):
    id: str
    slug: str
    origin_type: str = "plan"
    title: str
    destination: str
    destination_state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    group_size_min: int
    group_size_max: int
    cover_image_url: str | None = None
    gallery_urls: list[str] | None = None
    status: str
    plan_type: str = "STANDARD"
    vibes: list[str] | None = None
    group_type: str | None = None
    creator: UserSummary
    created_at: str


# ── Request schemas ───────────────────────────────────────────────────────────
class CreatePlanRequest(CamelModel):
    title: str = Field(..., min_length=5, max_length=300)
    destination: str = Field(..., min_length=2, max_length=200)
    destination_state: str | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_date_flexible: bool = False
    budget_min: int | None = Field(None, ge=0)
    budget_max: int | None = Field(None, ge=0)
    group_size_min: int = Field(default=1, ge=1)
    group_size_max: int = Field(default=10, ge=1)
    vibes: list[str] | None = None
    accommodation: str | None = None
    group_type: str | None = None
    gender_pref: str | None = None
    activities: list[str] | None = None
    itinerary: list[ItineraryItem] | None = None
    gallery_urls: list[str] | None = None
    cover_image_url: str | None = None
    auto_approve: bool = False
    plan_type: str = "STANDARD"


class UpdatePlanRequest(CamelModel):
    title: str | None = None
    destination: str | None = None
    destination_state: str | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_date_flexible: bool | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    group_size_min: int | None = None
    group_size_max: int | None = None
    vibes: list[str] | None = None
    accommodation: str | None = None
    group_type: str | None = None
    gender_pref: str | None = None
    activities: list[str] | None = None
    itinerary: list[dict] | None = None
    gallery_urls: list[str] | None = None
    cover_image_url: str | None = None


class ReferPlanRequest(CamelModel):
    agency_ids: list[str] = Field(..., min_length=1)


class ConfirmPlanRequest(CamelModel):
    offer_id: str
