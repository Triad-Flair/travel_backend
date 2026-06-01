from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

from sqlalchemy import ForeignKey, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin, TimestampsMixin


class Offer(TimestampsMixin, BaseModel):
    __tablename__ = "offers"
    __table_args__ = {"extend_existing": True}

    plan_id: Mapped[str] = mapped_column("planId", String(36), ForeignKey("plans.id"), nullable=False, index=True)
    agency_id: Mapped[str] = mapped_column("agencyId", String(36), ForeignKey("agencies.id"), nullable=False, index=True)
    price_per_person: Mapped[int] = mapped_column("pricePerPerson", Integer, nullable=False)
    pricing_tiers: Mapped[list | dict | None] = mapped_column("pricingTiers", JSONB, nullable=True)
    inclusions: Mapped[list | dict | None] = mapped_column("inclusions", JSONB, nullable=True)
    itinerary: Mapped[list | dict | None] = mapped_column("itinerary", JSONB, nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column("cancellationPolicy", Text, nullable=True)
    cancellation_rules: Mapped[list | dict | None] = mapped_column("cancellationRules", JSONB, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column("validUntil", DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column("status", PgEnum("PENDING","COUNTERED","ACCEPTED","REJECTED","WITHDRAWN",name="OfferStatus",create_type=False), default="PENDING", nullable=False, index=True)
    is_referred: Mapped[bool] = mapped_column("isReferred", Boolean, default=False, nullable=False)
    referred_at: Mapped[datetime | None] = mapped_column("referredAt", DateTime(timezone=True), nullable=True)

    plan = relationship("Plan", back_populates="offers")
    agency = relationship("Agency", back_populates="offers")
    negotiations = relationship("OfferNegotiation", back_populates="offer", lazy="noload")


class OfferNegotiation(CreatedAtMixin, BaseModel):
    """offer_negotiations: has createdAt only, no updatedAt."""
    __tablename__ = "offer_negotiations"
    __table_args__ = {"extend_existing": True}

    offer_id: Mapped[str] = mapped_column("offerId", String(36), ForeignKey("offers.id"), nullable=False, index=True)
    round: Mapped[int] = mapped_column("round", Integer, default=1, nullable=False)
    sender_type: Mapped[str] = mapped_column("senderType", String(20), nullable=False)  # "user" | "agency"
    price: Mapped[int | None] = mapped_column("price", Integer, nullable=True)
    inclusions_delta: Mapped[list | dict | None] = mapped_column("inclusionsDelta", JSONB, nullable=True)
    message: Mapped[str | None] = mapped_column("message", Text, nullable=True)

    offer = relationship("Offer", back_populates="negotiations")
