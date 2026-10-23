"""Safe projection of existing checks, preparation and delivery records."""
from studio.service import Problem


class LegacyDrafts:
    def __init__(self, app):
        self.app = app

    def get(self, article_id):
        post = self.app.post(article_id)
        flow = self.app.article_flow.get(article_id)
        # The explicit start promotes draft/idea to ready. Preview that gate without saving.
        candidate = {**post, "stage": "ready"} if post["stage"] != "published" else post
        report = self.app.article_flow.check(candidate)
        job = flow["job"]
        if job:
            receipt = self.app.db.one(
                "SELECT external_id,updated_at FROM jobs WHERE id=?", (job["id"],))
            job = {**job, **(receipt or {})}
        return {"post": post, "report": report, "intent": flow["intent"],
                "preparation": flow["preparation"], "job": job}

    def command(self, article_id, action, values):
        post = self.app.post(article_id)
        if post["version"] != values["version"]:
            raise Problem("文章已有新版本，请返回文章载入最新版本后再操作", 409)
        if post["archived"] or post["stage"] == "published":
            raise Problem("请使用未归档、未发表的文章", 409)
        flow = self.app.article_flow
        if action == "start":
            flow.request(article_id, values)
        elif action == "cancel":
            flow.cancel(article_id, values)
        else:
            current = flow.get(article_id)
            if current["job"] or not current["intent"] or current["intent"]["status"] != "waiting":
                raise Problem("当前没有等待补齐的送稿流程，请刷新状态", 409)
            if action == "prepare":
                # Serialize with cancel/resume so a late retry cannot restart a canceled intent.
                with flow.lock:
                    latest = flow.get(article_id)
                    if latest["job"] or not latest["intent"] or latest["intent"]["status"] != "waiting":
                        raise Problem("送稿等待已结束，请刷新状态", 409)
                    self.app.preparation.request(article_id, {"version": values["version"], "retry": True})
            else:
                preparation = current["preparation"]
                if not preparation or preparation["id"] != values.get("preparation_id"):
                    raise Problem("补齐建议不属于当前文章或已被替换", 409)
                self.app.preparation.confirm(preparation["id"], values)
        return self.get(article_id)
