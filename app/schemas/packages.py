from pydantic import Field

from app.schemas.base import CamelModel
from app.schemas.common import AgencyCard, AgencyPublicSummary
from app.schemas.plans import GroupSummary, ItineraryItem


class PackageCardResponse(CamelModel):
    """Lightweight package card for list/discover endpoints.
    No itinerary, cancellation rules, inclusions, or full gallery — just what
    the card UI needs. Agency shrinks to AgencyCard (4 fields vs 15)."""
    id: str
    slug: str
    title: str
    destination: str
    destination_state: str | None = None
    thumbnail_url: str | None = None
    base_price: int
    start_date: str | None = None
    end_date: str | None = None
    duration_days: int | None = None
    group_size_min: int
    group_size_max: int
    vibes: list[str] | None = None
    status: str
    agency: AgencyCard
    avg_rating: float = 0.0
    review_count: int = 0
    created_at: str


# Matches frontend PackageDetails exactly
class PackageDetails(CamelModel):
    id: str
    slug: str
    title: str
    destination: str
    destination_state: str | None = None
    itinerary: list[ItineraryItem] | None = None
    start_date: str | None = None
    end_date: str | None = None
    departure_dates: list[str] | None = None
    base_price: int
    pricing_tiers: list[dict] | None = None
    group_size_min: int
    group_size_max: int
    inclusions: dict | list | None = None
    exclusions: str | None = None
    accommodation: str | None = None
    vibes: list[str] | None = None
    activities: list[str] | None = None
    gallery_urls: list[str] | None = None
    cancellation_policy: str | None = None
    cancellation_rules: dict | None = None
    status: str
    agency: AgencyPublicSummary
    group: GroupSummary | None = None
    created_at: str
    updated_at: str


# Lightweight for discover/list (same keys as PackageDetails but fewer fields)
class PackageMeta(CamelModel):
    id: str
    slug: str
    origin_type: str = "package"
    title: str
    destination: str
    destination_state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    base_price: int
    group_size_min: int
    group_size_max: int
    gallery_urls: list[str] | None = None
    vibes: list[str] | None = None
    status: str
    agency: AgencyPublicSummary
    created_at: str


# ── Request schemas ───────────────────────────────────────────────────────────
class CreatePackageRequest(CamelModel):
    title: str = Field(..., min_length=5, max_length=300)
    destination: str = Field(..., min_length=2, max_length=200)
    destination_state: str | None = None
    base_price: int = Field(..., ge=100)
    group_size_min: int = Field(default=1, ge=1)
    group_size_max: int = Field(default=20, ge=1)
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    departure_dates: list[str] | None = None
    pricing_tiers: list[dict] | None = None
    inclusions: dict | None = None
    exclusions: str | None = None
    accommodation: str | None = None
    vibes: list[str] | None = None
    activities: list[str] | None = None
    gallery_urls: list[str] | None = None
    cancellation_policy: str | None = None
    cancellation_rules: dict | None = None
    itinerary: list[dict] | None = None


class UpdatePackageRequest(CamelModel):
    title: str | None = None
    destination: str | None = None
    destination_state: str | None = None
    base_price: int | None = None
    group_size_min: int | None = None
    group_size_max: int | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    gallery_urls: list[str] | None = None
    vibes: list[str] | None = None
    activities: list[str] | None = None
    inclusions: dict | None = None
    exclusions: str | None = None
    accommodation: str | None = None
    cancellation_policy: str | None = None
    itinerary: list[dict] | None = None
