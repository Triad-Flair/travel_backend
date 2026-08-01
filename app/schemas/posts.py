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


class PostCommentResponse(CamelModel):
    id: str
    post_id: str
    author: UserSummary
    content: str
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
    created_at: str
