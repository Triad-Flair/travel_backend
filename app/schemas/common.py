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
    """Matches frontend AgencySummary."""
    id: str
    name: str
    slug: str
    logo_url: str | None = None
    description: str | None = None
    verification: str | None = None
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


# Legacy aliases used in some services
UserMeta = UserSummary
AgencyMeta = AgencySummary
