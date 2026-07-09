from datetime import datetime

from app.schemas.base import CamelModel


class OkResponse(CamelModel):
    ok: bool = True
    message: str = "Success"


class AgencyCard(CamelModel):
    """Minimal agency for card/list views — avoids shipping 15-field AgencySummary in every row."""
    id: str
    name: str
    slug: str
    logo_url: str | None = None
    avg_rating: float = 0.0


# These match the frontend TypeScript types exactly
class UserSummary(CamelModel):
    """Matches frontend UserSummary."""
    id: str
    full_name: str
    username: str | None = None
    avatar_url: str | None = None
    verification: str | None = None
    gender: str | None = None
    city: str | None = None
    avg_rating: float = 0.0
    completed_trips: int = 0


class AgencySummary(CamelModel):
    """Matches frontend AgencySummary. Includes GST/PAN — only ever build this
    from an authenticated, relationship-gated context (e.g. an offer between
    the plan creator and the bidding agency, or an agency's own profile
    fetch). Never use this on a public/general-purpose listing endpoint —
    use AgencyPublicSummary there instead."""
    id: str
    name: str
    slug: str
    logo_url: str | None = None
    description: str | None = None
    verification: str | None = None
    verification_rejection_reason: str | None = None
    gstin: str | None = None
    pan: str | None = None
    tourism_license: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    state: str | None = None
    specializations: list[str] | None = None
    destinations: list[str] | None = None
    avg_rating: float = 0.0
    total_reviews: int = 0
    total_trips: int = 0


class AgencyPublicSummary(CamelModel):
    """Same shape as AgencySummary minus GST/PAN — for public, unauthenticated
    surfaces (agency browse/profile, package/plan detail pages), where GST/PAN
    are treated as sensitive and must not be exposed."""
    id: str
    name: str
    slug: str
    logo_url: str | None = None
    description: str | None = None
    verification: str | None = None
    tourism_license: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    state: str | None = None
    specializations: list[str] | None = None
    destinations: list[str] | None = None
    avg_rating: float = 0.0
    total_reviews: int = 0
    total_trips: int = 0


# Legacy aliases used in some services
UserMeta = UserSummary
AgencyMeta = AgencySummary
