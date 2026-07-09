from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import BadRequestError, NotFoundError
from app.models.location import City, State
from app.schemas.locations import CityResponse, StateResponse


def _state_to_response(state: State) -> StateResponse:
    return StateResponse(id=state.id, name=state.name, code=state.code)


def _city_to_response(city: City) -> CityResponse:
    return CityResponse(id=city.id, name=city.name, state_id=city.state_id)


async def list_states(db: AsyncSession) -> list[StateResponse]:
    result = await db.execute(select(State).order_by(State.name.asc()))
    return [_state_to_response(s) for s in result.scalars().all()]


async def list_cities(db: AsyncSession, state_id: str) -> list[CityResponse]:
    state = await db.scalar(select(State).where(State.id == state_id))
    if not state:
        raise NotFoundError("State")
    result = await db.execute(select(City).where(City.state_id == state_id).order_by(City.name.asc()))
    return [_city_to_response(c) for c in result.scalars().all()]


async def find_state_by_name(db: AsyncSession, name: str) -> State | None:
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    return await db.scalar(select(State).where(func.lower(State.name) == cleaned.lower()))


async def validate_state_name(db: AsyncSession, name: str | None) -> None:
    """Strict: the state list is complete, so an unrecognized value is a real
    input error. Only validates when a value is provided — state stays
    optional at the signup-field level."""
    if not name or not name.strip():
        return
    state = await find_state_by_name(db, name)
    if not state:
        raise BadRequestError(f"'{name}' is not a recognized Indian state or union territory")


async def is_known_city(db: AsyncSession, state_id: str | None, city_name: str | None) -> bool:
    """Lenient by design — this seed dataset covers major/tourist-hub cities
    only, not every town. Used as a soft signal (e.g. for analytics or
    autocomplete confirmation), never to reject a signup outright."""
    if not state_id or not city_name or not city_name.strip():
        return False
    match = await db.scalar(
        select(City).where(City.state_id == state_id, func.lower(City.name) == city_name.strip().lower())
    )
    return match is not None
