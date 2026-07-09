from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.exceptions import ConflictError
from app.schemas.auth import AgencySignupRequest
from app.services.auth import signup_agency_owner


@pytest.mark.asyncio
async def test_agency_signup_rejects_existing_owner_phone():
    db = AsyncMock()
    db.scalar = AsyncMock(
        side_effect=[
            SimpleNamespace(id="state-uttar-pradesh"),  # state lookup (agencyState="Uttar Pradesh")
            None,  # email lookup
            None,  # username lookup
            SimpleNamespace(id="existing-user"),  # phone lookup
        ]
    )

    request = AgencySignupRequest(
        full_name="Agency Owner",
        username="agency_owner",
        email="owner@example.com",
        phone="9876543210",
        password="Password123",
        city="Noida",
        travel_preferences="Adventure trips and curated group tours",
        agency_name="Trail Root",
        agency_description="Curated itineraries for weekend and long-distance travel.",
        agency_city="Noida",
        agency_state="Uttar Pradesh",
        specializations=["Adventure"],
        destinations=["Pan India"],
    )

    with pytest.raises(ConflictError, match="Phone already registered"):
        await signup_agency_owner(db, request)

    db.add.assert_not_called()
    db.flush.assert_not_called()
