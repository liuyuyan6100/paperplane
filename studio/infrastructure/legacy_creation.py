"""Reuse persisted workflows, article revisions and one creation worker."""

import hashlib
import json
import threading
from pathlib import Path

from studio.db import dumps, now, uid
from studio.editor_assistant import signature
from studio.service import Problem
from studio.workflows import record


class LegacyCreation:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        with app.db.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS creation_material_requests ("
                "request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "post_id TEXT NOT NULL REFERENCES posts(id))"
            )

    def materials(self, query=""):
        # Return summaries only. Full text is read only when a material is selected.
        rows = self.app.db.rows(
            "SELECT id,title,summary,source,version,stage FROM posts WHERE archived=0 "
            "ORDER BY updated_at DESC,rowid DESC"
        )
        return {
            "materials": [r for r in rows if query.lower() in (r["title"] + r["summary"]).lower()]
        }

    def shared(self, query=""):
        from studio.shared_library import listing

        data = listing(query)
        return {
            "items": [
                {k: r[k] for k in ("id", "name", "version", "source")}
                for r in data.get("items", [])
                if Path(r["name"]).suffix.lower() in (".md", ".txt", ".csv")
            ]
        }

    def material(self, values):
        fingerprint = hashlib.sha256(dumps(values).encode()).hexdigest()
        with self.lock:
            prior = self.app.db.one(
                "SELECT * FROM creation_material_requests WHERE request_id=?",
                (values["request_id"],),
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise Problem("请求编号已用于另一份素材", 409)
                return self.app.post(prior["post_id"])
            if values.get("shared_id"):
                from studio.shared_library import material

                item, raw = material(values["shared_id"], values["shared_version"])
                if Path(item["name"]).suffix.lower() not in (".md", ".txt", ".csv"):
                    raise Problem("本流程支持 Markdown、TXT 和 CSV 文字素材")
                try:
                    body = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise Problem("文字素材需要 UTF-8 编码") from None
                post = {
                    "title": Path(item["name"]).stem[:150],
                    "body": body,
                    "source": f"共享资料 {item['id']} v{item['version']} · {item['source']}/{item['relative_path']} · SHA256 {item['sha256']}",
                }
            else:
                post = {k: values[k] for k in ("title", "body", "source")}
            if not post["body"].strip():
                raise Problem("素材内容不能为空")
            with self.app.db.connect(immediate=True) as db:
                # Another HTTP adapter may have committed the same material request.
                prior = db.execute(
                    "SELECT * FROM creation_material_requests WHERE request_id=?",
                    (values["request_id"],),
                ).fetchone()
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        raise Problem("请求编号已用于另一份素材", 409)
                    return self.app.post(prior["post_id"])
                saved = self.app.save_post({**post, "stage": "idea"}, connection=db)
                db.execute(
                    "INSERT INTO creation_material_requests VALUES(?,?,?)",
                    (values["request_id"], fingerprint, saved["id"]),
                )
            return saved

    def listing(self, post_id):
        self.app.post(post_id)
        return {"workflows": [self.present(t) for t in self.app.workflows.list(post_id)]}

    def owned(self, post_id, task_id):
        task = self.app.workflows.get(task_id)
        if task["post_id"] != post_id:
            raise Problem("本篇创作任务不存在", 404)
        return task

    @staticmethod
    def present(task):
        # No raw snapshots, configuration, model route dictionaries or execution tokens.
        keys = (
            "id",
            "post_id",
            "post_title",
            "post_version",
            "reference_ids",
            "instruction",
            "status",
            "stage",
            "stage_label",
            "outline",
            "outline_version",
            "confirmed_outline_version",
            "selected_title",
            "title_options",
            "parent_id",
            "result_signature",
            "error",
            "created_at",
            "updated_at",
            "applied_version",
        )
        value = {k: task[k] for k in keys}
        value["references"] = task.get("references", [])
        value["input_changes"] = task.get("input_changes", [])
        value["events"] = task.get("events", [])
        result = task.get("result", {})
        value["result"] = {k: result[k] for k in ("title", "body", "review") if k in result}
        return value

    def get(self, post_id, task_id):
        return self.present(self.owned(post_id, task_id))

    def create(self, post_id, values):
        with self.lock:
            marker = {"preview_creation": {k: values[k] for k in ("reference_ids", "instruction")}}
            prior = self.app.db.one(
                "SELECT post_id,post_version,parent_id,snapshot FROM workflows WHERE id=?",
                (values["request_id"],),
            )
            if prior and (
                prior["post_id"] != post_id
                or prior["post_version"] != values["version"]
                or prior["parent_id"] != (values.get("parent_id") or "")
                or json.loads(prior["snapshot"]).get("creation_request") != marker
            ):
                raise Problem("请求编号已用于不同的创作要求", 409)
            if values.get("parent_id"):
                self.owned(post_id, values["parent_id"])
            task = self.app.workflows.create(
                {**values, "post_id": post_id, "creation_request": marker}
            )
            return self.present(task)

    def change(self, post_id, task_id, action, values):
        with self.lock:
            self.owned(post_id, task_id)
            if action == "adopt":
                return self.adopt(post_id, task_id, values)
            manager = self.app.workflows
            if action == "outline":
                task = manager.outline_edit(task_id, values)
            elif action == "confirm":
                task = manager.confirm(task_id, values)
            else:
                task = manager.result_edit(task_id, values)
            return self.present(task)

    def adopt(self, post_id, task_id, values):
        # Article/history and the adopted marker commit together; preserve the original input.
        with self.app.db.connect(immediate=True) as db:
            row = db.execute(
                "SELECT * FROM workflows WHERE id=? AND post_id=?", (task_id, post_id)
            ).fetchone()
            current = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
            if row["status"] != "applied":
                result = json.loads(row["result"])
                if row["status"] != "review" or signature(result) != values["result_signature"]:
                    raise Problem("待审稿已经变化，请重新核对", 409)
                if current["archived"] or current["version"] != values["version"]:
                    raise Problem("当前稿已被其他页面修改，请重新读取对照后再确认", 409)
                conflict = current["version"] != row["post_version"]
                if conflict and values["resolution"] == "unchanged":
                    raise Problem("生成期间原稿有修改，请先对照并选择使用待审稿或手动合并", 409)
                if values["resolution"] == "merged":
                    content = values["content"]
                    if not content:
                        raise Problem("请提供合并后的标题和正文")
                else:
                    content = {k: result[k] for k in ("title", "body")}
                saved = self.app.save_post(
                    {
                        **content,
                        "version": current["version"],
                        "stage": "draft",
                        "_edit_origin": "generated",
                    },
                    post_id,
                    connection=db,
                )
                if values["resolution"] == "merged":
                    db.execute(
                        "INSERT INTO workflow_body_edits(id,workflow_id,snapshot,created_at) VALUES(?,?,?,?)",
                        (uid(), task_id, dumps(result), now()),
                    )
                    db.execute(
                        "UPDATE workflows SET result=? WHERE id=?",
                        (dumps({**result, **content}), task_id),
                    )
                db.execute(
                    "UPDATE workflows SET status='applied',stage='completed',applied_at=?,"
                    "applied_version=?,updated_at=? WHERE id=?",
                    (now(), saved["version"], now(), task_id),
                )
                record(
                    db,
                    task_id,
                    "applied",
                    "completed",
                    "使用者核对后采用"
                    + ("手动合并稿" if values["resolution"] == "merged" else "待审正文")
                    + "；原修订保留，未送稿",
                )
        return {"workflow": self.get(post_id, task_id), "post": self.app.post(post_id)}
