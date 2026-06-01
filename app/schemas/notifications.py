from datetime import datetime

from app.schemas.base import CamelModel


class NotificationResponse(CamelModel):
    id: str
    type: str
    title: str
    body: str
    href: str | None = None
    metadata: dict | None = None
    read: bool
    read_at: datetime | None
    created_at: datetime


class ProfileViewResponse(CamelModel):
    id: str
    created_at: datetime
    target_type: str
    target_handle: str
    target_name: str
    viewer: dict
