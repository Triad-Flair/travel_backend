from datetime import datetime

from sqlalchemy import ForeignKey, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin, TimestampsMixin


class ChatMessage(CreatedAtMixin, BaseModel):
    """chat_messages: createdAt only, no updatedAt."""
    __tablename__ = "chat_messages"
    __table_args__ = {"extend_existing": True}

    group_id: Mapped[str] = mapped_column("groupId", String(36), ForeignKey("groups.id"), nullable=False, index=True)
    sender_id: Mapped[str | None] = mapped_column("senderId", String(36), ForeignKey("users.id"), nullable=True)
    message_type: Mapped[str] = mapped_column("messageType", String(20), default="TEXT", nullable=False)
    content: Mapped[str | None] = mapped_column("content", Text, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # Anti-leakage moderation (PRD 2.2) — see app/services/chat_moderation.py.
    # original_content is only ever populated when flagged=True, and is only
    # readable via the admin-only audit endpoint.
    original_content: Mapped[str | None] = mapped_column("originalContent", Text, nullable=True)
    flagged: Mapped[bool] = mapped_column("flagged", Boolean, default=False, nullable=False)
    flagged_categories: Mapped[list | None] = mapped_column("flaggedCategories", JSONB, nullable=True)

    group = relationship("Group", back_populates="messages")
    sender = relationship("User", lazy="noload", foreign_keys=[sender_id],
                          primaryjoin="ChatMessage.sender_id == User.id")
    poll = relationship("Poll", back_populates="message", uselist=False, lazy="noload")


class DirectConversation(TimestampsMixin, BaseModel):
    """direct_conversations: id, key, createdAt, updatedAt.
    Participants are in direct_conversation_participants junction table."""
    __tablename__ = "direct_conversations"
    __table_args__ = {"extend_existing": True}

    key: Mapped[str] = mapped_column("key", String(100), nullable=False, unique=True, index=True)

    participants = relationship("DirectConversationParticipant", back_populates="conversation", lazy="noload")
    messages = relationship("DirectMessage", back_populates="conversation", lazy="noload")


class DirectConversationParticipant(CreatedAtMixin, BaseModel):
    __tablename__ = "direct_conversation_participants"
    __table_args__ = {"extend_existing": True}

    conversation_id: Mapped[str] = mapped_column("conversationId", String(36), ForeignKey("direct_conversations.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False, index=True)
    last_read_at: Mapped[datetime | None] = mapped_column("lastReadAt", DateTime(timezone=True), nullable=True)

    conversation = relationship("DirectConversation", back_populates="participants")
    user = relationship("User", lazy="noload")


class DirectMessage(CreatedAtMixin, BaseModel):
    """direct_messages: createdAt only."""
    __tablename__ = "direct_messages"
    __table_args__ = {"extend_existing": True}

    conversation_id: Mapped[str] = mapped_column("conversationId", String(36), ForeignKey("direct_conversations.id"), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column("senderId", String(36), ForeignKey("users.id"), nullable=False)
    content: Mapped[str | None] = mapped_column("content", Text, nullable=True)
    original_content: Mapped[str | None] = mapped_column("originalContent", Text, nullable=True)
    flagged: Mapped[bool] = mapped_column("flagged", Boolean, default=False, nullable=False)
    flagged_categories: Mapped[list | None] = mapped_column("flaggedCategories", JSONB, nullable=True)

    conversation = relationship("DirectConversation", back_populates="messages")
    sender = relationship("User", lazy="noload", foreign_keys=[sender_id],
                          primaryjoin="DirectMessage.sender_id == User.id")


class ChatModerationKeyword(CreatedAtMixin, BaseModel):
    """Config-driven platform-leakage keyword list (PRD 2.2) — editable by
    admins via app/api/v1/chat.py without a redeploy. See
    app/services/chat_moderation.py for how this feeds the detector."""
    __tablename__ = "chat_moderation_keywords"
    __table_args__ = {"extend_existing": True}

    keyword: Mapped[str] = mapped_column("keyword", String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column("isActive", Boolean, default=True, nullable=False)


class Poll(TimestampsMixin, BaseModel):
    __tablename__ = "polls"
    __table_args__ = {"extend_existing": True}

    message_id: Mapped[str] = mapped_column("messageId", String(36), ForeignKey("chat_messages.id"), nullable=False, unique=True)
    group_id: Mapped[str] = mapped_column("groupId", String(36), ForeignKey("groups.id"), nullable=False)
    question: Mapped[str] = mapped_column("question", Text, nullable=False)
    options: Mapped[str] = mapped_column("options", Text, nullable=False)
    is_closed: Mapped[bool] = mapped_column("isClosed", Boolean, default=False, nullable=False)
    closes_at: Mapped[datetime | None] = mapped_column("closesAt", DateTime(timezone=True), nullable=True)

    message = relationship("ChatMessage", back_populates="poll")
    votes = relationship("PollVote", back_populates="poll", lazy="noload")


class PollVote(CreatedAtMixin, BaseModel):
    __tablename__ = "poll_votes"
    __table_args__ = {"extend_existing": True}

    poll_id: Mapped[str] = mapped_column("pollId", String(36), ForeignKey("polls.id"), nullable=False)
    user_id: Mapped[str] = mapped_column("userId", String(36), ForeignKey("users.id"), nullable=False)
    option_index: Mapped[int] = mapped_column("optionIndex", Integer, nullable=False)

    poll = relationship("Poll", back_populates="votes")
    user = relationship("User", lazy="noload")
