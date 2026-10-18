"""Typed routes for material selection and confirmed article creation."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.api.articles import ArticleId
from studio.application.creation import CreationService


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MaterialInput(Input):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    title: str = Field(default="", max_length=150)
    body: str = Field(default="", max_length=200000)
    source: str = Field(default="", max_length=2000)
    shared_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    shared_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def complete(self):
        if self.shared_id and (
            self.shared_version is None or self.title or self.body or self.source
        ):
            raise ValueError("共享资料与手写素材不能混用")
        if not self.shared_id and (
            not self.title.strip() or not self.body.strip() or self.shared_version is not None
        ):
            raise ValueError("请填写素材标题与内容")
        return self


class CreationInput(Input):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    version: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=4000)
    reference_ids: list[str] = Field(default_factory=list, max_length=6)
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    reuse_confirmed: bool = False


class OutlineInput(Input):
    outline_version: int = Field(ge=1)
    outline: str = Field(min_length=1, max_length=30000)
    selected_title: str = Field(min_length=1, max_length=150)


class Content(Input):
    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=200000)


class BodyInput(Input):
    result_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: Content


class AdoptInput(Input):
    version: int = Field(ge=1)
    result_signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolution: Literal["unchanged", "candidate", "merged"] = "unchanged"
    content: Content | None = None


def creation_router(service: CreationService):
    router = APIRouter(prefix="/api/v2")

    @router.get("/creation/materials")
    def materials(query: str = Query(default="", max_length=200)):
        return service.materials(query)

    @router.get("/creation/shared")
    def shared(query: str = Query(default="", max_length=200)):
        return service.shared(query)

    @router.post("/creation/materials")
    def add_material(values: MaterialInput):
        return service.material(values.model_dump())

    @router.get("/articles/{post_id}/creation")
    def listing(post_id: ArticleId):
        return service.listing(post_id)

    @router.post("/articles/{post_id}/creation")
    def create(post_id: ArticleId, values: CreationInput):
        return service.create(post_id, values.model_dump())

    @router.get("/articles/{post_id}/creation/{task_id}")
    def get(post_id: ArticleId, task_id: ArticleId):
        return service.get(post_id, task_id)

    @router.post("/articles/{post_id}/creation/{task_id}/outline")
    def outline(post_id: ArticleId, task_id: ArticleId, values: OutlineInput):
        return service.change(post_id, task_id, "outline", values.model_dump())

    @router.post("/articles/{post_id}/creation/{task_id}/confirm")
    def confirm(post_id: ArticleId, task_id: ArticleId, values: OutlineInput):
        return service.change(post_id, task_id, "confirm", values.model_dump())

    @router.post("/articles/{post_id}/creation/{task_id}/body")
    def body(post_id: ArticleId, task_id: ArticleId, values: BodyInput):
        return service.change(post_id, task_id, "body", values.model_dump())

    @router.post("/articles/{post_id}/creation/{task_id}/adopt")
    def adopt(post_id: ArticleId, task_id: ArticleId, values: AdoptInput):
        return service.change(post_id, task_id, "adopt", values.model_dump())

    return router
