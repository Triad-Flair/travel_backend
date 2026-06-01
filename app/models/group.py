from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin


class Group(CreatedAtMixin, BaseModel):
    """groups table: has createdAt but NO updatedAt."""
    __tablename__ = "groups"
    __table_args__ = {"extend_existing": True}

    plan_id: Mapped[str | None] = mapped_column("planId", String(36), ForeignKey("plans.id"), nullable=True, index=True)
    package_id: Mapped[str | None] = mapped_column("packageId", String(36), ForeignKey("packages.id"), nullable=True)
    current_size: Mapped[int] = mapped_column("currentSize", Integer, default=0, nullable=False)
    male_count: Mapped[int] = mapped_column("maleCount", Integer, default=0, nullable=False)
    female_count: Mapped[int] = mapped_column("femaleCount", Integer, default=0, nullable=False)
    other_count: Mapped[int] = mapped_column("otherCount", Integer, default=0, nullable=False)
    is_locked: Mapped[bool] = mapped_column("isLocked", Boolean, default=False, nullable=False)
    payment_window_closes_at: Mapped[datetime | None] = mapped_column(
        "paymentWindowEndsAt", DateTime(timezone=True), nullable=True
    )

    plan = relationship("Plan", back_populates="group", lazy="noload")
    package = relationship("Package", lazy="noload")
    members = relationship("GroupMember", back_populates="group", lazy="noload")
    messages = relationship("ChatMessage", back_populates="group", lazy="noload")
    payments = relationship("Payment", back_populates="group", lazy="noload")


class GroupMember(BaseModel):
    """group_members: has NO createdAt or updatedAt."""
    __tablename__ = "group_members"
    __table_args__ = {"extend_existing": True}

    group_id: Mapped[str] = mapped_column("groupId", String(36), ForeignKey("groups.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column("role", PgEnum("CREATOR","MEMBER",name="MemberRole",create_type=False), default="MEMBER", nullable=False)
    status: Mapped[str] = mapped_column("status", PgEnum("INTERESTED","APPROVED","COMMITTED","LEFT","REMOVED",name="MemberStatus",create_type=False), default="INTERESTED", nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column("joinedAt", DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column("committedAt", DateTime(timezone=True), nullable=True)
    left_at: Mapped[datetime | None] = mapped_column("leftAt", DateTime(timezone=True), nullable=True)

    group = relationship("Group", back_populates="members")
    user = relationship("User", back_populates="group_memberships")
