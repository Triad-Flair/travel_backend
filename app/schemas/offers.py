from pydantic import Field

from app.schemas.base import CamelModel
from app.schemas.common import AgencySummary, UserSummary


class PricingTier(CamelModel):
    min_pax: int
    price: int


class SubmitOfferRequest(CamelModel):
    plan_id: str | None = None
    price_per_person: int = Field(..., ge=100)
    pricing_tiers: list[PricingTier] | None = None
    inclusions: dict | None = None
    itinerary: list[dict] | None = None
    message: str | None = None
    cancellation_policy: str | None = None
    cancellation_rules: dict | None = None
    valid_until: str | None = None


class CounterOfferRequest(CamelModel):
    price: int = Field(..., ge=100)
    message: str | None = None
    inclusions_delta: dict | None = None


class RejectOfferRequest(CamelModel):
    reason: str | None = None


class OfferNegotiationResponse(CamelModel):
    id: str
    round: int
    sender_type: str
    price: int | None = None
    inclusions_delta: dict | None = None
    message: str | None = None
    created_at: str


class OfferPlanContext(CamelModel):
    id: str
    slug: str
    title: str
    destination: str
    budget_min: int | None = None
    budget_max: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str
    creator: UserSummary | None = None


class OfferResponse(CamelModel):
    id: str
    plan_id: str
    price_per_person: int
    pricing_tiers: list[PricingTier] | None = None
    inclusions: dict | None = None
    itinerary: list[dict] | None = None
    cancellation_policy: str | None = None
    cancellation_rules: dict | None = None
    valid_until: str | None = None
    status: str
    is_referred: bool = False
    referred_at: str | None = None
    created_at: str
    updated_at: str
    agency: AgencySummary
    negotiations: list[OfferNegotiationResponse] = []
    plan: OfferPlanContext | None = None
