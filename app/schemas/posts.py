from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel
from app.schemas.common import UserSummary

MAX_POST_IMAGES = 6


class CreatePostRequest(CamelModel):
    caption: str | None = None
    image_urls: list[str] = Field(default_factory=list, max_length=MAX_POST_IMAGES)
    destination: str | None = None


class CreateCommentRequest(CamelModel):
    content: str = Field(..., min_length=1, max_length=2000)
    parent_comment_id: str | None = None


class PostCommentResponse(CamelModel):
    id: str
    post_id: str
    author: UserSummary
    parent_comment_id: str | None = None
    content: str
    like_count: int = 0
    liked_by_me: bool = False
    created_at: str


class PostResponse(CamelModel):
    id: str
    author: UserSummary
    caption: str | None = None
    image_urls: list[str] = []
    destination: str | None = None
    like_count: int
    comment_count: int
    share_count: int
    liked_by_me: bool = False
    saved_by_me: bool = False
    author_followed_by_me: bool = False
    created_at: str


class PostFeedPageResponse(CamelModel):
    items: list[PostResponse]
    next_cursor: str | None = None


class CreatePostReportRequest(CamelModel):
    reason: Literal["SPAM", "HARASSMENT", "HATEFUL", "MISINFORMATION", "INAPPROPRIATE", "OTHER"]
    details: str | None = Field(default=None, max_length=1000)


class PostReportResponse(CamelModel):
    id: str
    status: str
    created_at: str
