"""Typed, article-scoped image plans and explicit preview/adoption endpoints."""

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from studio.api.articles import ArticleId
from studio.api.creation import Input
from studio.application.illustrations import IllustrationService


class CreatePlan(Input):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    post_version: int = Field(ge=1)


class Version(Input):
    version: int = Field(ge=1)


class Page(Input):
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(default="", max_length=500)
    prompt: str = Field(min_length=1, max_length=5000)
    slot: Literal["cover", "body", "xhs"]
    aspect: Literal["landscape", "portrait", "square"]
    placement: str = Field(default="", pattern=r"^(?:|legacy_end|[ps]_[a-f0-9]{64})$")
    caption: str = Field(default="", max_length=300)


class EditPlan(Version):
    pages: list[Page] = Field(min_length=1, max_length=12)


class Generate(Version):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    page_index: int = Field(ge=0, le=11)
    instruction: str = Field(default="", max_length=2000)
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ImportOriginal(Version):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    page_index: int = Field(ge=0, le=11)
    image_url: str = Field(pattern=r"^/media/[a-f0-9]{32}\.(?:png|jpg|webp)$")


class Adoption(Input):
    candidate_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    post_version: int = Field(ge=1)
    placement: str = Field(default="", pattern=r"^(?:|legacy_end|[ps]_[a-f0-9]{64})$")
    caption: str = Field(default="", max_length=300)
    ack_changes: bool = False


class Archive(Version):
    archived: bool


class Upload(Input):
    data: str = Field(min_length=1, max_length=6666668)
    title: str = Field(min_length=1, max_length=150)


def illustration_router(service: IllustrationService):
    router = APIRouter(prefix="/api/v2")

    @router.post("/illustrations/upload")
    def upload(values: Upload):
        return service.upload(values.model_dump())

    @router.get("/articles/{post_id}/illustrations")
    def listing(post_id: ArticleId, archived: bool = False):
        return service.listing(post_id, archived)

    @router.post("/articles/{post_id}/illustrations")
    def create(post_id: ArticleId, values: CreatePlan):
        return service.create(post_id, values.model_dump())

    @router.get("/articles/{post_id}/illustrations/{plan_id}")
    def get(post_id: ArticleId, plan_id: ArticleId):
        return service.get(post_id, plan_id)

    @router.post("/articles/{post_id}/illustrations/{plan_id}/edit")
    def edit(post_id: ArticleId, plan_id: ArticleId, values: EditPlan):
        return service.change(post_id, plan_id, "edit", values.model_dump())

    @router.post("/articles/{post_id}/illustrations/{plan_id}/improve")
    def improve(post_id: ArticleId, plan_id: ArticleId, values: Version):
        return service.change(post_id, plan_id, "improve", values.model_dump())

    @router.post("/articles/{post_id}/illustrations/{plan_id}/generate")
    def generate(post_id: ArticleId, plan_id: ArticleId, values: Generate):
        return service.change(post_id, plan_id, "generate", values.model_dump())

    @router.post("/articles/{post_id}/illustrations/{plan_id}/import-original")
    def original(post_id: ArticleId, plan_id: ArticleId, values: ImportOriginal):
        return service.change(post_id, plan_id, "import_original", values.model_dump())

    @router.post("/articles/{post_id}/illustrations/{plan_id}/preview")
    def preview(post_id: ArticleId, plan_id: ArticleId, values: Adoption):
        return service.change(post_id, plan_id, "preview", values.model_dump())

    @router.post("/articles/{post_id}/illustrations/{plan_id}/adopt")
    def adopt(post_id: ArticleId, plan_id: ArticleId, values: Adoption):
        return service.change(post_id, plan_id, "adopt", values.model_dump())

    @router.post("/articles/{post_id}/illustrations/{plan_id}/archive")
    def archive(post_id: ArticleId, plan_id: ArticleId, values: Archive):
        return service.change(post_id, plan_id, "archive", values.model_dump())

    return router
