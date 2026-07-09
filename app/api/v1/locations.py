from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.locations import CityResponse, StateResponse
from app.services import locations as loc_svc

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/states", response_model=list[StateResponse])
async def get_states(db: AsyncSession = Depends(get_db)):
    return await loc_svc.list_states(db)


@router.get("/states/{state_id}/cities", response_model=list[CityResponse])
async def get_cities(state_id: str, db: AsyncSession = Depends(get_db)):
    return await loc_svc.list_cities(db, state_id)
