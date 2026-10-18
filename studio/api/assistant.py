"""Typed M2 assistant API; mutations retain the shared same-origin boundary."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from studio.api.articles import ArticleId, ArticleInput
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
    model: str | None = Field(default=None, max_length=200, pattern=r"^[^\x00-\x1f]*$")
    draft: Content
    selection: Selection | None = None
    reference_turn_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class Adoption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft: Content


class MetadataMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    draft: ArticleInput


class MetadataAdoption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft: ArticleInput


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["api"] = "api"
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=200)


class TextModels(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chat: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=200)


class ImageConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    use_shared: bool | None = None
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=2000)
    model: str | None = Field(default=None, max_length=200)
    cover_model: str | None = Field(default=None, max_length=200)
    illustration_model: str | None = Field(default=None, max_length=200)
    references: Literal["edits", "none"] | None = None


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    connection: Connection | None = None
    text_models: TextModels | None = None
    image: ImageConnection | None = None


def assistant_router(service: AssistantService):
    router = APIRouter(prefix="/api/v2")

    @router.get("/articles/{article_id}/metadata")
    def metadata(article_id: ArticleId):
        return service.metadata(article_id)

    @router.post("/articles/{article_id}/metadata")
    def suggest_metadata(article_id: ArticleId, values: MetadataMessage):
        data = values.model_dump()
        data["draft"].pop("version", None)
        return service.suggest_metadata(article_id, data)

    @router.post("/articles/{article_id}/metadata/{turn_id}/accept")
    def accept_metadata(article_id: ArticleId, turn_id: ArticleId, values: MetadataAdoption):
        return service.accept_metadata(
            article_id,
            turn_id,
            {
                "draft": values.draft.model_dump(exclude={"version"}),
            },
        )

    @router.get("/assistant/configuration")
    def configuration():
        return service.configuration()

    @router.get("/assistant/settings")
    def settings():
        return service.settings()

    @router.post("/assistant/settings")
    def save_settings(values: Settings):
        return service.save_settings(values.model_dump(exclude_unset=True, exclude_none=True))

    @router.get("/assistant/models")
    def models(kind: Literal["text", "image"] = "text"):
        return service.models(kind)

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
