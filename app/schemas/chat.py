from datetime import datetime

from pydantic import Field
from pydantic import model_validator

from app.schemas.base import CamelModel
from app.schemas.common import UserSummary


class SendMessageRequest(CamelModel):
    content: str | None = None
    media_url: str | None = None
    message_type: str = "text"
    metadata: dict | None = None


class ChatMessageResponse(CamelModel):
    id: str
    group_id: str
    sender_id: str | None = None
    sender: UserSummary | None = None
    message_type: str
    content: str | None = None
    metadata: dict | None = None
    created_at: str


class CreatePollRequest(CamelModel):
    question: str = Field(..., min_length=3)
    options: list[str] = Field(..., min_length=2, max_length=8)
    closes_at: datetime | None = None


class VotePollRequest(CamelModel):
    option_id: str


class PollResponse(CamelModel):
    id: str
    question: str
    options: list[str]
    vote_counts: list[int]
    user_vote: int | None = None
    is_closed: bool = False
    closes_at: datetime | None = None
    total_votes: int = 0


class DirectMessageResponse(CamelModel):
    id: str
    conversation_id: str
    sender_id: str
    sender: UserSummary | None = None
    content: str | None = None
    metadata: dict | None = None
    created_at: str


class ConversationResponse(CamelModel):
    id: str
    created_at: str
    updated_at: str
    counterpart: UserSummary | None = None
    unread_count: int = 0
    last_read_at: str | None = None
    last_message: DirectMessageResponse | None = None


class LastMessageSummary(CamelModel):
    """Truncated message preview for the inbox — avoids serialising full sender objects."""
    preview: str
    created_at: str
    is_mine: bool


class ConversationInboxItem(CamelModel):
    """Slim inbox row returned by GET /direct/conversations.
    Flat counterpart fields (no nested UserSummary) + 60-char message preview."""
    id: str
    updated_at: str
    counterpart_id: str | None = None
    counterpart_name: str | None = None
    counterpart_avatar: str | None = None
    unread_count: int = 0
    last_message: LastMessageSummary | None = None


class SendDirectMessageRequest(CamelModel):
    content: str | None = None
    media_url: str | None = None
    metadata: dict | None = None


class CreateConversationRequest(CamelModel):
    target_user_id: str

    @model_validator(mode="before")
    @classmethod
    def normalize_target_field(cls, values):
        if not isinstance(values, dict):
            return values
        if "target_user_id" in values and values["target_user_id"]:
            return values
        if "targetUserId" in values and values["targetUserId"]:
            values["target_user_id"] = values["targetUserId"]
            return values
        if "user_id" in values and values["user_id"]:
            values["target_user_id"] = values["user_id"]
            return values
        if "userId" in values and values["userId"]:
            values["target_user_id"] = values["userId"]
        return values
