"""Versioned, same-origin draft commands; viewing has no side effects."""
from typing import Annotated, Literal

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from studio.application.drafts import DraftService


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    preparation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def draft_router(service: DraftService):
    router = APIRouter(prefix="/api/v2/articles")

    @router.get("/{article_id}/draft")
    def get(article_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]):
        return service.get(article_id)

    @router.post("/{article_id}/draft/{action}")
    def command(article_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")],
                action: Literal["start", "cancel", "prepare", "confirm"], values: DraftInput):
        return service.command(article_id, action, values.model_dump(exclude_none=True))

    return router
