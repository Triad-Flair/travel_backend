from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel, CreatedAtMixin


class State(CreatedAtMixin, BaseModel):
    __tablename__ = "states"
    __table_args__ = {"extend_existing": True}

    name: Mapped[str] = mapped_column("name", String(100), nullable=False, unique=True)
    code: Mapped[str] = mapped_column("code", String(10), nullable=False, unique=True)

    cities = relationship("City", back_populates="state", lazy="noload")


class City(CreatedAtMixin, BaseModel):
    __tablename__ = "cities"
    __table_args__ = {"extend_existing": True}

    name: Mapped[str] = mapped_column("name", String(100), nullable=False)
    state_id: Mapped[str] = mapped_column("stateId", String(36), ForeignKey("states.id"), nullable=False, index=True)

    state = relationship("State", back_populates="cities")
