from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin


class Follow(CreatedAtMixin, BaseModel):
    __tablename__ = "follows"
    __table_args__ = {"extend_existing": True}

    follower_user_id: Mapped[str] = mapped_column("followerUserId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(
        "targetType",
        PgEnum("USER", "AGENCY", name="FollowTargetType", create_type=False),
        nullable=False,
    )
    target_user_id: Mapped[str | None] = mapped_column("targetUserId", String(36), nullable=True, index=True)
    target_agency_id: Mapped[str | None] = mapped_column("targetAgencyId", String(36), nullable=True)

    follower = relationship("User", foreign_keys=[follower_user_id],
                            primaryjoin="Follow.follower_user_id == User.id", lazy="noload")


class ProfileView(CreatedAtMixin, BaseModel):
    __tablename__ = "profile_views"
    __table_args__ = {"extend_existing": True}

    viewer_user_id: Mapped[str | None] = mapped_column("viewerUserId", String(36), nullable=True)
    target_owner_user_id: Mapped[str | None] = mapped_column("targetOwnerUserId", String(36), nullable=True)
    target_type: Mapped[str] = mapped_column(
        "targetType",
        PgEnum("USER", "AGENCY", name="ProfileViewTargetType", create_type=False),
        nullable=False,
    )
    target_user_id: Mapped[str | None] = mapped_column("targetUserId", String(36), nullable=True)
    target_agency_id: Mapped[str | None] = mapped_column("targetAgencyId", String(36), nullable=True)


class Review(CreatedAtMixin, BaseModel):
    __tablename__ = "reviews"
    __table_args__ = {"extend_existing": True}

    reviewer_id: Mapped[str] = mapped_column("reviewerId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    review_type: Mapped[str] = mapped_column("reviewType", String(20), nullable=False)
    target_agency_id: Mapped[str | None] = mapped_column("targetAgencyId", String(36), nullable=True, index=True)
    target_user_id: Mapped[str | None] = mapped_column("targetUserId", String(36), nullable=True)
    group_id: Mapped[str | None] = mapped_column("groupId", String(36), nullable=True)
    overall_rating: Mapped[int] = mapped_column("overallRating", Integer, nullable=False)
    safety_rating: Mapped[int] = mapped_column("safetyRating", Integer, nullable=False)
    value_rating: Mapped[int] = mapped_column("valueRating", Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column("comment", Text, nullable=True)

    reviewer = relationship("User", lazy="noload", foreign_keys=[reviewer_id],
                            primaryjoin="Review.reviewer_id == User.id")


class Notification(CreatedAtMixin, BaseModel):
    __tablename__ = "notifications"
    __table_args__ = {"extend_existing": True}

    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column("type", String(40), nullable=False)
    title: Mapped[str] = mapped_column("title", String(200), nullable=False)
    body: Mapped[str] = mapped_column("body", Text, nullable=False)
    href: Mapped[str | None] = mapped_column("href", Text, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column("readAt", DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="notifications")
