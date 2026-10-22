"""Typed M3 task queries and explicit, versioned recovery commands."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from studio.api.articles import ArticleId
from studio.application.tasks import TaskService
from studio.domain.tasks import TaskAction, TaskGroup, TaskKind, TaskVisibility
from typing import Literal


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: TaskAction
    verified: bool = False


class TaskRecord(BaseModel):
    kind: TaskKind
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    title: str
    post_id: str
    owner_id: str
    kind_label: str
    status: str
    status_label: str
    group: TaskGroup
    overdue: bool
    stage: str
    stage_label: str
    description: str
    error: str
    error_kind: str
    created_at: str
    updated_at: str
    attempts: int | None
    revision: str
    visibility: TaskVisibility
    visibility_revision: str
    can_manage: bool
    actions: list[TaskAction]
    legacy_url: str
    instruction: str


class Event(BaseModel):
    at: str
    status: str
    message: str


class Receipt(BaseModel):
    external_id: str
    url: str


class TaskDetail(TaskRecord):
    events: list[Event]
    receipt: Receipt | None


class KindInfo(BaseModel):
    id: TaskKind
    label: str


class Worker(BaseModel):
    alive: bool
    error: str


class TaskPage(BaseModel):
    tasks: list[TaskRecord]
    counts: dict[str, int]
    total: int
    offset: int
    limit: int
    kinds: list[KindInfo]
    workers: dict[str, Worker]
    checked_at: str


class VisibilityItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: TaskKind
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class VisibilityChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["archive", "trash", "restore"]
    items: list[VisibilityItem] = Field(min_length=1, max_length=100)


def task_router(service: TaskService):
    router = APIRouter(prefix="/api/v2/tasks")

    @router.get("", response_model=TaskPage)
    def listing(
        group: TaskGroup = "all",
        kind: TaskKind | None = None,
        post_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}$"),
        query: str = Query(default="", max_length=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=50, ge=1, le=100),
        view: TaskVisibility = "active",
    ):
        return service.listing(
            group=group, kind=kind, post_id=post_id, query=query, offset=offset, limit=limit, view=view
        )

    @router.post("/visibility")
    def change_visibility(values: VisibilityChange):
        return service.change_visibility(values.action, [item.model_dump() for item in values.items])

    @router.get("/{kind}/{task_id}", response_model=TaskDetail)
    def detail(kind: TaskKind, task_id: ArticleId):
        return service.detail(kind, task_id)

    @router.post("/{kind}/{task_id}/commands")
    def command(kind: TaskKind, task_id: ArticleId, values: Command):
        return service.command(kind, task_id, values.model_dump())

    return router
