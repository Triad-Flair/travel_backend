from unittest.mock import AsyncMock

import pydantic
import pytest

from app.exceptions import BadRequestError
from app.schemas.auth import AgencySignupRequest, TravelerSignupRequest
from app.services.locations import validate_state_name


def _valid_traveler_kwargs(**overrides):
    kwargs = dict(
        full_name="Traveler One",
        username="traveler_one",
        phone="9876543210",
        password="Password123",
    )
    kwargs.update(overrides)
    return kwargs


def test_traveler_phone_accepts_valid_indian_mobile():
    req = TravelerSignupRequest(**_valid_traveler_kwargs(phone="9876543210"))
    assert req.phone == "9876543210"


@pytest.mark.parametrize(
    "bad_phone",
    [
        "1234567890",  # starts with 1, not 6-9
        "0987654321",  # starts with 0
        "98765432100",  # 11 digits (max_length allows it through Field, regex must catch it)
        "abcdefghij",  # non-numeric
    ],
)
def test_traveler_phone_rejects_invalid_numbers(bad_phone):
    with pytest.raises(pydantic.ValidationError):
        TravelerSignupRequest(**_valid_traveler_kwargs(phone=bad_phone))


def test_agency_phone_fields_both_optional_when_omitted():
    req = AgencySignupRequest(
        full_name="Owner",
        username="agency_owner",
        email="owner@example.com",
        password="Password123",
        agency_name="Some Agency",
    )
    assert req.phone is None
    assert req.agency_phone is None


def test_agency_phone_rejects_invalid_number_when_provided():
    with pytest.raises(pydantic.ValidationError):
        AgencySignupRequest(
            full_name="Owner",
            username="agency_owner",
            email="owner@example.com",
            password="Password123",
            agency_name="Some Agency",
            agency_phone="0000000000",
        )


@pytest.mark.asyncio
async def test_validate_state_name_passes_for_known_state():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=object())  # State found
    await validate_state_name(db, "Karnataka")  # should not raise


@pytest.mark.asyncio
async def test_validate_state_name_rejects_unknown_state():
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # no matching State row
    with pytest.raises(BadRequestError, match="not a recognized"):
        await validate_state_name(db, "Narnia")


@pytest.mark.asyncio
async def test_validate_state_name_skips_when_not_provided():
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=AssertionError("should not query when state is None"))
    await validate_state_name(db, None)  # should not raise, should not query
