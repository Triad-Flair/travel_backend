from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model: serialises to camelCase so the frontend gets the exact field names it expects."""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,   # allow both snake and camel when reading
        from_attributes=True,
    )
