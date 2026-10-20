"""A narrow profile editor: name, direction, audience and goal only."""

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from studio.application.profiles import ProfileService

ProfileId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    direction: str = Field(max_length=2000)
    audience: str = Field(max_length=2000)
    goal: str = Field(max_length=2000)
    version: int | None = Field(default=None, ge=1)


def profile_router(service: ProfileService):
    router = APIRouter(prefix="/api/v2/profiles")

    @router.get("")
    def listing():
        return {"profiles": service.listing()}

    @router.get("/{profile_id}")
    def get(profile_id: ProfileId):
        return service.get(profile_id)

    @router.post("")
    def create(values: ProfileInput):
        return service.save(values.model_dump(exclude={"version"}))

    @router.post("/{profile_id}")
    def save(profile_id: ProfileId, values: ProfileInput):
        return service.save(values.model_dump(), profile_id)

    return router
