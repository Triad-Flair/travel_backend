from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, TimestampsMixin


class Package(TimestampsMixin, BaseModel):
    __tablename__ = "packages"
    __table_args__ = {"extend_existing": True}

    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column("title", String(300), nullable=False)
    slug: Mapped[str] = mapped_column("slug", String(300), nullable=False, unique=True)
    destination: Mapped[str] = mapped_column("destination", String(200), nullable=False)
    destination_state: Mapped[str | None] = mapped_column("destinationState", String(100), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column("startDate", DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column("endDate", DateTime(timezone=True), nullable=True)
    departure_dates: Mapped[list | dict | None] = mapped_column("departureDates", JSONB, nullable=True)
    price_per_person: Mapped[int] = mapped_column("basePrice", Integer, nullable=False)
    pricing_tiers: Mapped[list | dict | None] = mapped_column("pricingTiers", JSONB, nullable=True)
    group_size_min: Mapped[int] = mapped_column("groupSizeMin", Integer, default=1, nullable=False)
    group_size_max: Mapped[int] = mapped_column("groupSizeMax", Integer, default=20, nullable=False)
    inclusions: Mapped[list | dict | None] = mapped_column("inclusions", JSONB, nullable=True)
    exclusions: Mapped[str | None] = mapped_column("exclusions", Text, nullable=True)
    accommodation: Mapped[str | None] = mapped_column("accommodation", String(50), nullable=True)
    vibes: Mapped[list | dict | None] = mapped_column("vibes", JSONB, nullable=True)
    activities: Mapped[list | dict | None] = mapped_column("activities", JSONB, nullable=True)
    gallery_urls: Mapped[list | dict | None] = mapped_column("galleryUrls", JSONB, nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column("cancellationPolicy", Text, nullable=True)
    cancellation_rules: Mapped[list | dict | None] = mapped_column("cancellationRules", JSONB, nullable=True)
    status: Mapped[str] = mapped_column("status", PgEnum("DRAFT","OPEN","CONFIRMING","CONFIRMED","COMPLETED","EXPIRED","CANCELLED",name="PlanStatus",create_type=False), default="DRAFT", nullable=False, index=True)
    itinerary: Mapped[list | dict | None] = mapped_column("itinerary", JSONB, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column("expiresAt", DateTime(timezone=True), nullable=True)
    source_offer_id: Mapped[str | None] = mapped_column(
        "sourceOfferId", String(36), ForeignKey("offers.id"), nullable=True
    )
    expiry_warning_sent_at: Mapped[datetime | None] = mapped_column(
        "expiryWarningSentAt", DateTime(timezone=True), nullable=True
    )

    # Non-DB convenience attrs (no DB column)
    cover_image_url: str | None = None   # derived from first galleryUrls entry
    avg_rating: float = 0.0
    review_count: int = 0
    booking_count: int = 0
    is_featured: bool = False

    agency = relationship("Agency", back_populates="packages", lazy="noload")
