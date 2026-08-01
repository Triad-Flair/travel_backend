"""GET /users/search — regression test. search_users used to construct
UserMeta(display_name=...) where the field is actually full_name, which
raised a pydantic ValidationError on any real match (only an empty result
set avoided the crash). It also wrapped the response in make_pagination()
even though every frontend caller expects a plain list. Both are fixed;
this locks in the corrected shape.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.common import UserMeta


def _fake_user(**overrides):
    defaults = dict(
        id="user-1",
        username="isha_verma",
        display_name="Isha Verma",
        avatar_url=None,
        is_active=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_user_meta_accepts_full_name_not_display_name():
    # This is the exact construction search_users now uses — asserting it
    # doesn't raise is the regression guard for the old display_name= bug.
    user = _fake_user()
    meta = UserMeta(
        id=user.id,
        full_name=user.display_name or user.username or "",
        username=user.username,
        avatar_url=user.avatar_url,
    )
    assert meta.full_name == "Isha Verma"


@pytest.mark.asyncio
async def test_search_users_returns_plain_list_not_paginated_wrapper():
    from app.api.v1.users import search_users

    user = _fake_user()
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [user]
    db.execute = AsyncMock(return_value=result)

    response = await search_users(q="isha", page=1, page_size=20, db=db)

    assert isinstance(response, list)
    assert len(response) == 1
    assert response[0].full_name == "Isha Verma"
    assert response[0].username == "isha_verma"
