from app.schemas.base import CamelModel


class DiscoverItem(CamelModel):
    """Exact shape of frontend DiscoverItem type."""
    id: str
    slug: str
    origin_type: str          # "plan" | "package"
    title: str
    destination: str
    destination_state: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    price_low: int | None = None
    price_high: int | None = None
    vibes: list[str] | None = None
    group_type: str | None = None
    group_size_min: int = 1
    group_size_max: int = 20
    cover_image_url: str | None = None
    status: str
    created_at: str
    owner_id: str | None = None
    agency_id: str | None = None
    joined_count: int = 0


class DiscoverFilters(CamelModel):
    audience: str | None = None
    destination: str | None = None
    budget_min: int | None = None
    budget_max: int | None = None
    vibes: str | None = None
    origin_type: str | None = None
    plan_type: str | None = None
    group_type: str | None = None
    sort: str | None = None  # "recent" | "price_low" | "price_high" | "popular"
    page: int = 1
    page_size: int = 20


class SearchResult(CamelModel):
    items: list[DiscoverItem]
    total: int
    query: str
