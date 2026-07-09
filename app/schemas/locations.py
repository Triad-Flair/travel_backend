from app.schemas.base import CamelModel


class StateResponse(CamelModel):
    id: str
    name: str
    code: str


class CityResponse(CamelModel):
    id: str
    name: str
    state_id: str
