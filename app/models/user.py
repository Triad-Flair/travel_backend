from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, TimestampsMixin


class User(TimestampsMixin, BaseModel):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    email: Mapped[str | None] = mapped_column("email", String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column("phone", String(20), nullable=True, index=True)
    username: Mapped[str] = mapped_column("username", String(50), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column("fullName", String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column("avatarUrl", Text, nullable=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(
        "dateOfBirth", DateTime(timezone=False), nullable=True
    )
    gender: Mapped[str | None] = mapped_column("gender", String(20), nullable=True)
    location: Mapped[str | None] = mapped_column("city", String(100), nullable=True)
    bio: Mapped[str | None] = mapped_column("bio", Text, nullable=True)
    verification_tier: Mapped[str] = mapped_column(
        "verification",
        PgEnum("BASIC", "VERIFIED", "TRUSTED", name="VerificationTier", create_type=False),
        default="BASIC",
        nullable=False,
    )
    aadhaar_hash: Mapped[str | None] = mapped_column("aadhaarHash", String(255), nullable=True)
    completed_trips: Mapped[int] = mapped_column("completedTrips", Integer, default=0, nullable=False)
    avg_rating: Mapped[float] = mapped_column("avgRating", Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column("passwordHash", String(255), nullable=True)
    travel_style: Mapped[str | None] = mapped_column("travelPreferences", Text, nullable=True)
    referral_code: Mapped[str | None] = mapped_column("referralCode", String(20), nullable=True)
    email_verified: Mapped[bool] = mapped_column("emailVerified", Boolean, default=False, nullable=False)
    email_verification_token: Mapped[str | None] = mapped_column("emailVerificationToken", String(255), nullable=True)
    # role is not in the DB — derived from agency membership (agency admin = has AgencyMember row)
    role: str = "user"  # non-mapped, set at runtime

    plans = relationship("Plan", back_populates="creator", lazy="noload", foreign_keys="Plan.creator_id")
    group_memberships = relationship("GroupMember", back_populates="user", lazy="noload")
    notifications = relationship("Notification", back_populates="user", lazy="noload")
