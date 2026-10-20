"""Narrow topic routes for one profile and one written idea per plan."""

from typing import Annotated

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from studio.application.topics import TopicService

TopicId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TopicInput(Input):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    profile_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    idea: str = Field(min_length=1, max_length=12000)


class SelectInput(Input):
    index: int = Field(ge=0, le=2)


def topic_router(service: TopicService):
    router = APIRouter(prefix="/api/v2/topics")

    @router.get("")
    def listing():
        return {"plans": service.listing()}

    @router.get("/{plan_id}")
    def get(plan_id: TopicId):
        return service.get(plan_id)

    @router.post("")
    def create(values: TopicInput):
        return service.create(values.model_dump())

    @router.post("/{plan_id}/retry")
    def retry(plan_id: TopicId):
        return service.retry(plan_id)

    @router.post("/{plan_id}/select")
    def select(plan_id: TopicId, values: SelectInput):
        return service.select(plan_id, values.index)

    return router
