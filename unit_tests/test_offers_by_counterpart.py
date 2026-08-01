"""list_offers_by_agency_owner_for_traveler — the traveler-side counterpart
to list_offers_by_counterpart. DM conversation participants are plain users,
so a traveler's DM "counterpart" for an agency chat is that agency's owner
user account, not the agency id directly — this resolves owner user id ->
Agency before querying offers.
"""
from unittest.mock import AsyncMock

import pytest

from app.services.offers import list_offers_by_agency_owner_for_traveler


@pytest.mark.asyncio
async def test_returns_empty_list_when_no_agency_owned_by_counterpart():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)

    result = await list_offers_by_agency_owner_for_traveler(db, "traveler-1", "some-user-id")

    assert result == []
    db.scalar.assert_awaited_once()
