"""Typed M2 assistant API; mutations retain the shared same-origin boundary."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from studio.api.articles import ArticleId
from studio.application.assistant import AssistantService


class Content(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(max_length=150)
    body: str = Field(max_length=200000)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["discuss", "edit"]
    draft: Content
    selection: Selection | None = None
    reference_turn_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class Adoption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft: Content


def assistant_router(service: AssistantService):
    router = APIRouter(prefix="/api/v2")

    @router.get("/assistant/configuration")
    def configuration():
        return service.configuration()

    @router.get("/articles/{article_id}/assistant")
    def get(article_id: ArticleId):
        return service.get(article_id)

    @router.post("/articles/{article_id}/assistant")
    def send(article_id: ArticleId, message: Message):
        return service.send(article_id, message.model_dump())

    @router.post("/articles/{article_id}/assistant/{turn_id}/accept")
    def accept(article_id: ArticleId, turn_id: ArticleId, values: Adoption):
        return service.accept(article_id, turn_id, values.model_dump())

    @router.post("/articles/{article_id}/assistant/{turn_id}/retry")
    def retry(article_id: ArticleId, turn_id: ArticleId):
        return service.retry(article_id, turn_id)

    return router
