from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.database import get_db
from app.exceptions import ForbiddenError, UnauthorizedError
from app.models.enums import UserRole


def get_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError()
    return authorization.split(" ", 1)[1]


class CurrentUser:
    def __init__(
        self,
        token: str = Depends(get_token),
    ) -> None:
        self.payload = decode_access_token(token)

    @property
    def user_id(self) -> str:
        return self.payload["sub"]

    @property
    def role(self) -> UserRole:
        return UserRole(self.payload.get("role", "user"))

    @property
    def agency_id(self) -> str | None:
        return self.payload.get("agencyId")

    def require_role(self, role: UserRole) -> None:
        if self.role != role and self.role != UserRole.PLATFORM_ADMIN:
            raise ForbiddenError()

    def require_agency(self) -> str:
        if self.role not in (UserRole.AGENCY_ADMIN, UserRole.PLATFORM_ADMIN):
            raise ForbiddenError("Agency account required")
        if not self.agency_id:
            raise ForbiddenError("No agency associated with this account")
        return self.agency_id

    def require_admin(self) -> None:
        if self.role != UserRole.PLATFORM_ADMIN:
            raise ForbiddenError("Platform admin access required")


def get_current_user(token: str = Depends(get_token)) -> CurrentUser:
    return CurrentUser(token=token)


def get_optional_user(authorization: str | None = Header(default=None)) -> CurrentUser | None:
    if not authorization:
        return None
    try:
        return CurrentUser(token=authorization.split(" ", 1)[1])
    except Exception:
        return None
