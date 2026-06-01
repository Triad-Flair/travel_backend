from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, TimestampsMixin


class Plan(TimestampsMixin, BaseModel):
    __tablename__ = "plans"
    __table_args__ = {"extend_existing": True}

    creator_id: Mapped[str] = mapped_column("creatorId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column("title", String(300), nullable=False)
    slug: Mapped[str] = mapped_column("slug", String(300), nullable=False, unique=True)
    destination: Mapped[str] = mapped_column("destination", String(200), nullable=False)
    destination_state: Mapped[str | None] = mapped_column("destinationState", String(100), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column("startDate", DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column("endDate", DateTime(timezone=True), nullable=True)
    is_flexible_dates: Mapped[bool] = mapped_column("isDateFlexible", Boolean, default=False, nullable=False)
    budget_min: Mapped[int | None] = mapped_column("budgetMin", Integer, nullable=True)
    budget_max: Mapped[int | None] = mapped_column("budgetMax", Integer, nullable=True)
    group_size_min: Mapped[int] = mapped_column("groupSizeMin", Integer, default=1, nullable=False)
    group_size_max: Mapped[int] = mapped_column("groupSizeMax", Integer, default=10, nullable=False)
    vibes: Mapped[list | dict | None] = mapped_column("vibes", JSONB, nullable=True)
    accommodation: Mapped[str | None] = mapped_column("accommodation", String(50), nullable=True)
    group_type: Mapped[str | None] = mapped_column("groupType", String(50), nullable=True)
    gender_pref: Mapped[str | None] = mapped_column("genderPref", String(30), nullable=True)
    activities: Mapped[list | dict | None] = mapped_column("activities", JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column("description", Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column("coverImageUrl", Text, nullable=True)
    gallery_urls: Mapped[list | dict | None] = mapped_column("galleryUrls", JSONB, nullable=True)
    auto_approve: Mapped[bool] = mapped_column("autoApprove", Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column("status", PgEnum("DRAFT","OPEN","CONFIRMING","CONFIRMED","COMPLETED","EXPIRED","CANCELLED",name="PlanStatus",create_type=False), default="DRAFT", nullable=False, index=True)
    plan_type: Mapped[str] = mapped_column("planType", PgEnum("STANDARD","CORPORATE",name="PlanType",create_type=False), default="STANDARD", nullable=False)
    itinerary: Mapped[list | dict | None] = mapped_column("itinerary", JSONB, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column("expiresAt", DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column("confirmedAt", DateTime(timezone=True), nullable=True)
    confirmed_offer_id: Mapped[str | None] = mapped_column("selectedOfferId", String(36), nullable=True)

    creator = relationship("User", back_populates="plans", foreign_keys=[creator_id])
    offers = relationship("Offer", back_populates="plan", lazy="noload")
    group = relationship("Group", back_populates="plan", uselist=False, lazy="noload")
