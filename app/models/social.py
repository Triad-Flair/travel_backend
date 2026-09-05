from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin, TimestampsMixin


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


class Post(TimestampsMixin, BaseModel):
    __tablename__ = "posts"
    __table_args__ = {"extend_existing": True}

    author_user_id: Mapped[str] = mapped_column("authorUserId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    caption: Mapped[str | None] = mapped_column("caption", Text, nullable=True)
    image_urls: Mapped[list | None] = mapped_column("imageUrls", JSONB, nullable=True)
    destination: Mapped[str | None] = mapped_column("destination", String(120), nullable=True)
    like_count: Mapped[int] = mapped_column("likeCount", Integer, default=0, nullable=False)
    comment_count: Mapped[int] = mapped_column("commentCount", Integer, default=0, nullable=False)
    share_count: Mapped[int] = mapped_column("shareCount", Integer, default=0, nullable=False)

    author = relationship("User", lazy="noload", foreign_keys=[author_user_id],
                          primaryjoin="Post.author_user_id == User.id")


class PostLike(CreatedAtMixin, BaseModel):
    __tablename__ = "post_likes"
    __table_args__ = (UniqueConstraint("postId", "userId", name="uq_post_likes_post_user"), {"extend_existing": True})

    post_id: Mapped[str] = mapped_column("postId", String(36), ForeignKey("posts.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)


class PostSave(CreatedAtMixin, BaseModel):
    __tablename__ = "post_saves"
    __table_args__ = (UniqueConstraint("postId", "userId", name="uq_post_saves_post_user"), {"extend_existing": True})

    post_id: Mapped[str] = mapped_column("postId", String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)


class PostReport(CreatedAtMixin, BaseModel):
    __tablename__ = "post_reports"
    __table_args__ = (UniqueConstraint("postId", "reporterUserId", name="uq_post_reports_post_reporter"), {"extend_existing": True})

    post_id: Mapped[str] = mapped_column("postId", String(36), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    reporter_user_id: Mapped[str] = mapped_column("reporterUserId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column("reason", String(40), nullable=False)
    details: Mapped[str | None] = mapped_column("details", Text, nullable=True)
    status: Mapped[str] = mapped_column("status", String(20), default="OPEN", nullable=False, index=True)


class UserBlock(CreatedAtMixin, BaseModel):
    __tablename__ = "user_blocks"
    __table_args__ = (UniqueConstraint("blockerUserId", "blockedUserId", name="uq_user_blocks_blocker_blocked"), {"extend_existing": True})

    blocker_user_id: Mapped[str] = mapped_column("blockerUserId", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    blocked_user_id: Mapped[str] = mapped_column("blockedUserId", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)


class PostComment(CreatedAtMixin, BaseModel):
    __tablename__ = "post_comments"
    __table_args__ = {"extend_existing": True}

    post_id: Mapped[str] = mapped_column("postId", String(36), ForeignKey("posts.id"), nullable=False, index=True)
    author_user_id: Mapped[str] = mapped_column("authorUserId", String(36), ForeignKey("users.id"), nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column("parentCommentId", String(36), ForeignKey("post_comments.id"), nullable=True, index=True)
    content: Mapped[str] = mapped_column("content", Text, nullable=False)
    like_count: Mapped[int] = mapped_column("likeCount", Integer, default=0, nullable=False)

    author = relationship("User", lazy="noload", foreign_keys=[author_user_id],
                          primaryjoin="PostComment.author_user_id == User.id")


class PostCommentLike(CreatedAtMixin, BaseModel):
    __tablename__ = "post_comment_likes"
    __table_args__ = (UniqueConstraint("commentId", "userId", name="uq_post_comment_likes_comment_user"), {"extend_existing": True})

    comment_id: Mapped[str] = mapped_column("commentId", String(36), ForeignKey("post_comments.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False)


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
