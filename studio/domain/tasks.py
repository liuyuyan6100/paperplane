"""Task projections and execution ownership, independent of HTTP and SQLite."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import uuid4

TaskKind = Literal[
    "assistant",
    "workflow",
    "delivery",
    "topic",
    "repurpose",
    "illustration",
    "image",
    "visual",
    "review",
    "performance",
    "preparation",
    "conversation",
    "learning",
    "draft-intent",
]
TaskGroup = Literal["all", "active", "attention", "review", "done"]
TaskAction = Literal["retry", "cancel", "recheck", "not_received"]

LABELS = {
    "queued": "排队中",
    "running": "正在执行",
    "failed": "执行失败",
    "interrupted": "执行已中断",
    "uncertain": "结果待核对",
    "outline_review": "待确认提纲",
    "review": "待审阅",
    "ready": "待采用",
    "draft": "已送入平台草稿",
    "manual": "待人工处理",
    "waiting": "等待处理",
    "checking": "正在检查",
    "pending": "等待处理",
    "paused": "已暂停",
    "completed": "已完成",
    "applied": "已采用",
    "accepted": "已有取用记录",
    "published": "已发表",
    "canceled": "已取消",
    "stale": "依据已变化",
    "existing": "已关联送稿任务",
    "disabled": "已停用",
    "skipped": "已跳过",
    "selected": "已选择",
}


def group_for(kind: str, status: str, stage: str = "") -> str:
    if kind == "draft-intent" and status in ("queued", "existing"):
        return "done"
    if kind == "topic" and status == "completed" and stage == "selection":
        return "review"
    if kind == "assistant" and status == "review" and stage == "discuss":
        return "done"
    if status in ("queued", "running", "checking"):
        return "active"
    if status in ("failed", "interrupted", "uncertain", "stale"):
        return "attention"
    if status in (
        "outline_review",
        "review",
        "ready",
        "draft",
        "manual",
        "waiting",
        "pending",
        "paused",
    ):
        return "review"
    if status in (
        "completed",
        "applied",
        "accepted",
        "published",
        "canceled",
        "disabled",
        "skipped",
        "selected",
    ):
        return "done"
    return "attention"


def error_kind(status: str, error: str) -> str:
    if status == "uncertain":
        return "unknown_delivery"
    if status == "interrupted":
        return "interrupted"
    if status not in ("failed", "stale"):
        return "none"
    message = error.casefold()
    if any(
        word in message
        for word in ("401", "403", "认证", "密钥", "未配置", "api key", "unauthorized", "权限")
    ):
        return "configuration"
    if any(
        word in message
        for word in ("超时", "timeout", "网络", "连接", "429", "503", "繁忙", "rate limit")
    ):
        return "temporary"
    if any(word in message for word in ("json", "格式", "解析")):
        return "result_format"
    return "needs_review"


@dataclass(frozen=True)
class ExecutionLease:
    token: str
    attempt: int

    @classmethod
    def next(cls, attempts: int):
        return cls(uuid4().hex, attempts + 1)

    def owns(self, row) -> bool:
        return bool(row and row["status"] == "running" and row["run_token"] == self.token)

    def same_attempt(self, row) -> bool:
        return bool(row and row["attempts"] == self.attempt)


class TaskRepository(Protocol):
    def listing(
        self,
        *,
        group: str,
        kind: str | None,
        post_id: str | None,
        query: str,
        offset: int,
        limit: int,
    ) -> dict: ...
    def detail(self, kind: str, task_id: str) -> dict: ...
    def command(self, kind: str, task_id: str, values: dict) -> dict: ...
