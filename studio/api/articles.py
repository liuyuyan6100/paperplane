"""Typed article routes, with existing business validation retained."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from studio.application.articles import ArticleService
from studio.markdown import render

ArticleId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]


class ArticleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=150)
    body: str = Field(default="", max_length=200000)
    summary: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=60)
    tags: list[str] = Field(default_factory=list, max_length=12)
    cover: str = Field(default="", max_length=2000)
    source: str = Field(default="", max_length=2000)
    stage: Literal["idea", "draft", "ready", "published"] = "draft"
    profile_id: str | None = None
    version: int | None = Field(default=None, ge=1)


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    body: str = Field(max_length=200000)


class ArticleAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["archive", "unarchive", "trash", "restore"]
    version: int = Field(ge=1)


def article_router(service: ArticleService):
    router = APIRouter(prefix="/api/v2")

    @router.get("/articles")
    def listing(scope: Literal["active", "archived", "trash"] = "active"):
        return {"articles": service.listing(scope)}

    @router.get("/profiles")
    def profiles():
        return {"profiles": service.profiles()}

    @router.get("/articles/{article_id}")
    def get(article_id: ArticleId):
        return service.get(article_id)

    @router.post("/articles")
    def create(values: ArticleInput):
        return service.save(values.model_dump())

    @router.post("/articles/{article_id}")
    def save(article_id: ArticleId, values: ArticleInput):
        return service.save(values.model_dump(exclude_unset=True), article_id)

    @router.post("/articles/{article_id}/manage")
    def manage(article_id: ArticleId, values: ArticleAction):
        return service.manage(article_id, values.action, values.version)

    @router.post("/preview")
    def preview(values: PreviewInput):
        return {"html": render(values.body)}

    return router
