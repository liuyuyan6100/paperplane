"""Read existing task tables; recovery commands delegate to their original owners."""

import hashlib
import json
import threading
from dataclasses import dataclass

from studio.db import dumps, now
from studio.domain.tasks import LABELS, error_kind, group_for
from studio.markdown import safe_url
from studio.service import Problem

COMMAND_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_commands (
 request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, task_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
 result TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_commands_pending
 ON task_commands(kind,task_id) WHERE status='pending';
"""


def decoded(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def j(path):
    return f"CASE WHEN json_valid(t.snapshot) THEN json_extract(t.snapshot,'{path}') END"


@dataclass(frozen=True)
class Source:
    kind: str
    table: str
    label: str
    post: str = "t.post_id"
    owner: str = "t.post_id"
    stage: str = "''"
    description: str = "''"
    joins: str = ""
    condition: str = "1"
    key: str = "id"
    created: str = "t.created_at"
    updated: str = "t.updated_at"
    attempts: str = "NULL"

    def sql(self):
        return f"""SELECT '{self.kind}' kind,t.{self.key} id,t.status,{self.stage} stage,
          COALESCE(p.title,'{self.label}') title,COALESCE(p.id,'') post_id,
          COALESCE({self.owner},'') owner_id,{self.description} description,t.error,
          {self.created} created_at,{self.updated} updated_at,{self.attempts} attempts
          FROM {self.table} t {self.joins} LEFT JOIN posts p ON p.id={self.post}
          WHERE {self.condition}"""


SOURCES = (
    Source(
        "assistant",
        "editor_assistant_turns",
        "编辑对话",
        post=j("$.post.id"),
        owner="t.target_id",
        stage="t.mode",
        description="t.message",
    ),
    Source(
        "workflow",
        "workflows",
        "文章创作",
        stage="t.stage",
        description="t.instruction",
        attempts="t.attempts",
    ),
    Source(
        "delivery",
        "jobs",
        "渠道送稿",
        owner="t.channel_id",
        stage="t.channel_id",
        description="t.note",
        condition="t.deleted_at IS NULL",
        attempts="t.attempts",
    ),
    Source(
        "topic",
        "topic_plans",
        "选题计划",
        post="t.selected_post_id",
        owner="t.selected_post_id",
        stage="CASE WHEN t.selected_index IS NULL THEN 'selection' ELSE 'selected' END",
    ),
    Source(
        "repurpose",
        "repurpose_variants",
        "平台改编",
        post="t.source_post_id",
        owner="t.source_post_id",
        stage="t.platform",
        description="t.instruction",
    ),
    Source("illustration", "illustration_plans", "配图计划", condition="t.archived_at=''"),
    Source(
        "image",
        "illustration_candidates",
        "图片候选",
        post="parent.post_id",
        owner="t.plan_id",
        joins="JOIN illustration_plans parent ON parent.id=t.plan_id",
        condition="parent.archived_at=''",
    ),
    Source(
        "visual",
        "platform_visual_pages",
        "平台图稿",
        post="parent.source_post_id",
        owner="t.variant_id",
        joins="JOIN repurpose_variants parent ON parent.id=t.variant_id",
        created="parent.created_at",
        updated="parent.updated_at",
    ),
    Source("review", "editorial_reviews", "文章审稿"),
    Source(
        "performance",
        "performance_reviews",
        "效果复盘",
        post="parent.post_id",
        owner="t.job_id",
        joins="LEFT JOIN jobs parent ON parent.id=t.job_id",
    ),
    Source("preparation", "draft_preparations", "送稿信息补齐"),
    Source(
        "conversation",
        "conversation_turns",
        "创作对话",
        post="''",
        owner="t.conversation_id",
        description="t.message",
    ),
    Source(
        "learning",
        "profile_learning_events",
        "画像学习",
        post=j("$.post_id"),
        owner="t.profile_id",
        stage="t.kind",
    ),
    Source(
        "draft-intent",
        "article_draft_intents",
        "公众号送稿检查",
        owner="t.post_id",
        stage="t.channel_id",
        key="post_id",
        created="''",
        updated="t.updated_at",
    ),
)
BY_KIND = {source.kind: source for source in SOURCES}


def prepare_preview_restart(app):
    """Only the preview lifecycle calls this, before starting any workers."""
    with app.db.connect(immediate=True) as db:
        # Old queued generation must not turn into a fresh paid call at restart.
        for row in db.execute("SELECT id FROM workflows WHERE status='queued'").fetchall():
            message = "上次排队的创作未完成，未自动调用模型；请手动重试原任务"
            db.execute(
                "UPDATE workflows SET status='interrupted',run_token='',error=?,updated_at=? WHERE id=?",
                (message, now(), row["id"]),
            )
            from studio.workflows import record

            record(db, row["id"], "interrupted", "prepare", message)
        db.execute(
            "UPDATE draft_preparations SET status='interrupted',run_token='',error='上次补齐准备未完成，请到原文章核对后继续',updated_at=? WHERE status='queued'",
            (now(),),
        )
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_commands'"
        ).fetchone():
            db.execute(
                "UPDATE task_commands SET status='unknown',updated_at=? WHERE status='pending'",
                (now(),),
            )


class LegacyTasks:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        with app.db.connect() as db:
            db.executescript(COMMAND_SCHEMA)
        selects = []
        for source in SOURCES:
            sql = source.sql()
            # An intent has no independent error field; its report is inspected in its owner.
            if source.kind == "draft-intent":
                sql = sql.replace("t.error,", "'' error,")
            selects.append(sql)
        self.base = " UNION ALL ".join(selects)
        self.group_sql = (
            "CASE WHEN kind='draft-intent' AND status IN ('queued','existing') THEN 'done' "
            "WHEN kind='topic' AND status='completed' AND stage='selection' THEN 'review' "
            + " ".join(
                f"WHEN kind='{source.kind}' AND status='{state}' AND stage='{stage}' THEN '{group_for(source.kind, state, stage)}'"
                for source in SOURCES
                for state, stage in [("review", "discuss")]
                if source.kind == "assistant"
            )
            + " "
            + " ".join(f"WHEN status='{state}' THEN '{group_for('', state)}'" for state in LABELS)
            + " ELSE 'attention' END"
        )

    def _raw(self, db, kind, task_id):
        source = BY_KIND.get(kind)
        if not source:
            raise Problem("任务类型不存在", 404)
        raw = db.execute(
            f"SELECT * FROM {source.table} WHERE {source.key}=?", (task_id,)
        ).fetchone()
        if not raw:
            raise Problem("任务不存在", 404)
        return dict(raw)

    def _projected(self, db, kind, task_id):
        row = db.execute(
            "SELECT * FROM (" + self.base + ") WHERE kind=? AND id=?", (kind, task_id)
        ).fetchone()
        if not row:
            raise Problem("任务不存在或已归档，请到原工作台查看历史", 404)
        return dict(row)

    def _actions(self, row, raw):
        kind, state = row["kind"], row["status"]
        actions = []
        if kind in ("workflow", "topic", "repurpose", "review", "performance") and state in (
            "failed",
            "interrupted",
        ):
            actions.append("retry")
        if kind == "workflow":
            if state == "canceled":
                actions.append("retry")
            if (
                state in ("queued", "running", "outline_review", "review", "failed", "interrupted")
                and raw.get("stage") != "apply"
            ):
                actions.append("cancel")
        if kind == "assistant" and raw["kind"] != "image" and state in ("failed", "interrupted"):
            actions.append("retry")
        if kind == "delivery":
            if state == "failed":
                actions.append("retry")
            if state in ("queued", "failed", "manual"):
                actions.append("cancel")
            if state == "uncertain":
                actions.append("not_received")
            if state in ("uncertain", "draft") and raw.get("external_id"):
                mode = decoded(raw.get("snapshot")).get("delivery_config", {}).get("mode")
                from studio.draft_reconciliation import SUPPORTED

                if (raw["channel_id"] == "wechat" and mode == "api") or (
                    mode == "desktop-publishing" and raw["channel_id"] in SUPPORTED
                ):
                    actions.append("recheck")
        return actions

    def _present(self, row, db):
        raw = self._raw(db, row["kind"], row["id"])
        item = dict(row)
        item["kind_label"] = BY_KIND[row["kind"]].label
        item["group"] = group_for(row["kind"], row["status"], row["stage"])
        item["status_label"] = (
            "已回复"
            if row["kind"] == "assistant"
            and row["status"] == "review"
            and row["stage"] == "discuss"
            else LABELS.get(row["status"], "未知状态：" + row["status"])
        )
        if row["kind"] == "draft-intent" and row["status"] in ("queued", "existing"):
            item["status_label"] = "已交接送稿任务"
        if row["kind"] == "topic" and row["status"] == "completed" and row["stage"] == "selection":
            item["status_label"] = "待选择选题"
        item["stage_label"] = row["stage"] or item["status_label"]
        if row["kind"] == "workflow":
            from studio.workflows import STAGE_LABELS

            item["stage_label"] = STAGE_LABELS.get(row["stage"], item["status_label"])
        elif row["kind"] == "assistant":
            item["stage_label"] = "文章讨论" if row["stage"] == "discuss" else "修改建议"
        elif row["kind"] == "delivery":
            channel = db.execute(
                "SELECT name FROM channels WHERE id=?", (raw["channel_id"],)
            ).fetchone()
            item["stage_label"] = channel["name"] if channel else "平台交付"
        elif row["kind"] == "topic":
            item["stage_label"] = "选择题目" if row["stage"] == "selection" else "选题已选择"
        item["revision"] = digest(raw)
        item["error_kind"] = error_kind(row["status"], row["error"])
        item["actions"] = self._actions(row, raw)
        links = {
            "workflow": "workflows",
            "delivery": "queue",
            "topic": "topics",
            "repurpose": "repurpose",
            "illustration": "illustrations",
            "image": "illustrations",
            "visual": "repurpose",
            "review": "reviews",
            "performance": "analytics",
            "conversation": "chat",
            "learning": "profiles",
            "preparation": "edit",
            "draft-intent": "edit",
        }
        link = links.get(row["kind"], "edit")
        anchor = (
            row["owner_id"]
            if row["kind"] in ("image", "conversation", "repurpose", "visual")
            else row["id"]
            if row["kind"]
            in ("topic", "performance", "workflow", "delivery", "illustration", "review")
            else row["post_id"]
            if link == "edit"
            else ""
        )
        item["legacy_url"] = "/#" + link + ("/" + anchor if anchor else "")
        if row["kind"] == "assistant":
            context_kind = raw["kind"]
            item["legacy_url"] = (
                "/#edit/" + row["post_id"]
                if context_kind == "post"
                else "/#"
                + (
                    "workflows"
                    if context_kind in ("outline", "body")
                    else "repurpose"
                    if context_kind == "variant"
                    else "illustrations"
                )
            )
        item["description"] = str(item["description"] or "")[:4000]
        if row["status"] == "uncertain":
            instruction = (
                "先核对原平台是否已收到。核对期间不重发；只有明确确认未收到后，才可标记并手动重试。"
            )
        elif row["status"] == "interrupted":
            instruction = (
                "已保留原输入和已完成记录；不会因查看或重启自动重新生成。请核对后手动恢复。"
            )
        elif row["kind"] == "workflow" and row["status"] == "outline_review":
            instruction = "请到原创作任务确认提纲；未确认前不会生成正文。"
        elif item["group"] == "active":
            instruction = "任务在本机后台执行，可关闭任务中心继续编辑；刷新或切换页面不会重复发起。"
        elif row["kind"] == "assistant" and item["group"] == "done":
            instruction = "可回文章侧栏查看回复或取用记录；讨论不改正文，取用不等于已经保存。"
        elif item["group"] in ("review", "done"):
            instruction = "请在原工作项核对结果；任务中心不自动采用、保存或公开发表。"
        else:
            instruction = "按原工作项处理；重试使用原输入快照，不创建另一条任务。"
        item["instruction"] = instruction
        return item

    def listing(self, *, group="all", kind=None, post_id=None, query="", offset=0, limit=50):
        conditions, params = ["1"], []
        for name, value in [("kind", kind), ("post_id", post_id)]:
            if value:
                conditions.append(name + "=?")
                params.append(value)
        if query:
            conditions.append(
                "(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')"
            )
            value = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params.extend([value] * 3)
        scope = (
            "SELECT *,("
            + self.group_sql
            + ") task_group FROM ("
            + self.base
            + ") WHERE "
            + " AND ".join(conditions)
        )
        with self.app.db.connect() as db:
            db.execute("BEGIN")
            counts = {key: 0 for key in ("all", "active", "attention", "review", "done")}
            for row in db.execute(
                "SELECT task_group,COUNT(*) n FROM (" + scope + ") GROUP BY task_group", params
            ):
                counts[row["task_group"]] = row["n"]
                counts["all"] += row["n"]
            tail = "" if group == "all" else " WHERE task_group=?"
            scoped = params + ([] if group == "all" else [group])
            rows = db.execute(
                "SELECT * FROM ("
                + scope
                + ")"
                + tail
                + " ORDER BY updated_at DESC,kind,id LIMIT ? OFFSET ?",
                (*scoped, limit, offset),
            ).fetchall()
            tasks = [self._present(dict(row), db) for row in rows]
        return {
            "tasks": tasks,
            "counts": counts,
            "total": counts[group],
            "offset": offset,
            "limit": limit,
            "kinds": [{"id": s.kind, "label": s.label} for s in SOURCES],
            "workers": self.workers(),
            "checked_at": now(),
        }

    def workers(self):
        return {
            "delivery": {
                "alive": bool(self.app.worker and self.app.worker.is_alive()),
                "error": self.app.worker_error,
            },
            "creation": {
                "alive": bool(self.app.workflows.worker and self.app.workflows.worker.is_alive()),
                "error": self.app.workflows.worker_error,
            },
        }

    def detail(self, kind, task_id):
        with self.app.db.connect() as db:
            db.execute("BEGIN")
            row = self._projected(db, kind, task_id)
            item = self._present(row, db)
            raw = self._raw(db, kind, task_id)
            events = []
            if kind in ("workflow", "delivery"):
                table, owner = (
                    ("workflow_events", "workflow_id")
                    if kind == "workflow"
                    else ("events", "job_id")
                )
                events = [
                    dict(e)
                    for e in db.execute(
                        f"SELECT at,status,message FROM {table} WHERE {owner}=? ORDER BY id DESC LIMIT 100",
                        (task_id,),
                    )
                ][::-1]
            elif kind == "conversation":
                events = [
                    {"at": "", "status": e["status"], "message": e["name"]}
                    for e in db.execute(
                        "SELECT name,status FROM conversation_steps WHERE turn_id=? ORDER BY ordinal",
                        (task_id,),
                    )
                ]
            item["events"] = events
            item["receipt"] = (
                {
                    "external_id": str(raw.get("external_id") or ""),
                    "url": safe_url(raw.get("url") or "") or "",
                }
                if kind == "delivery"
                else None
            )
            if kind == "workflow":
                item["description"] = str(raw.get("instruction") or "")[:4000]
            return item

    def command(self, kind, task_id, values):
        fingerprint = digest(
            [kind, task_id, values["action"], values["revision"], values.get("verified", False)]
        )
        request_id = values["request_id"]
        with self.lock:
            with self.app.db.connect(immediate=True) as db:
                prior = db.execute(
                    "SELECT * FROM task_commands WHERE request_id=?", (request_id,)
                ).fetchone()
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        raise Problem("同一操作编号不能更换内容", 409)
                    result = decoded(prior["result"])
                    if prior["status"] == "succeeded":
                        return result
                    if prior["status"] == "failed":
                        raise Problem(result.get("error", "操作未完成"), result.get("status", 409))
                    raise Problem(
                        "上次操作结果尚未确认，请刷新任务核对；不会自动重复执行",
                        409,
                        {"command_status": "unknown"},
                    )
                row = self._projected(db, kind, task_id)
                raw = self._raw(db, kind, task_id)
                if digest(raw) != values["revision"]:
                    raise Problem("任务已变化，请刷新后核对当前状态再操作", 409)
                if values["action"] not in self._actions(row, raw):
                    raise Problem("当前任务不支持此操作；结果待核对时不能直接重试", 409)
                if values["action"] == "not_received" and values.get("verified") is not True:
                    raise Problem("请先到原平台核实未收到内容，再明确勾选确认", 409)
                if db.execute(
                    "SELECT 1 FROM task_commands WHERE kind=? AND task_id=? AND status='pending'",
                    (kind, task_id),
                ).fetchone():
                    raise Problem("此任务已有操作正在处理，请刷新状态", 409)
                db.execute(
                    "INSERT INTO task_commands(request_id,kind,task_id,fingerprint,action,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
                    (request_id, kind, task_id, fingerprint, values["action"], now(), now()),
                )
            try:
                self._dispatch(kind, task_id, values, raw)
            except Exception as exc:
                known = isinstance(exc, Problem)
                result = {
                    "error": str(exc) if known else "操作结果尚未确认，请刷新任务核对，不自动重试",
                    "status": exc.status if known else 409,
                }
                with self.app.db.connect(immediate=True) as db:
                    db.execute(
                        "UPDATE task_commands SET status=?,result=?,updated_at=? WHERE request_id=?",
                        ("failed" if known else "unknown", dumps(result), now(), request_id),
                    )
                if known:
                    raise
                raise Problem(result["error"], 409, {"command_status": "unknown"}) from None
            result = {"ok": True, "kind": kind, "id": task_id, "action": values["action"]}
            with self.app.db.connect(immediate=True) as db:
                db.execute(
                    "UPDATE task_commands SET status='succeeded',result=?,updated_at=? WHERE request_id=?",
                    (dumps(result), now(), request_id),
                )
            return result

    def _dispatch(self, kind, task_id, values, raw):
        action = values["action"]
        if kind == "delivery":
            if action == "recheck":
                if raw["channel_id"] == "wechat":
                    return self.app.recheck_wechat(task_id)
                from studio.draft_reconciliation import reconcile

                return reconcile(self.app, task_id, {})
            return self.app.job_action(task_id, action, {"verified": values.get("verified", False)})
        if kind == "workflow":
            return getattr(self.app.workflows, action)(task_id)
        if kind == "assistant":
            return self.app.editor_assistant.retry(task_id)
        if kind == "topic":
            return self.app.topics.retry(task_id)
        if kind == "repurpose":
            return self.app.repurpose.retry(task_id)
        if kind == "review":
            return self.app.reviews.retry(task_id, {})
        if kind == "performance":
            return self.app.performance.retry(task_id, {})
        raise Problem("请从原工作项核对后继续", 409)
