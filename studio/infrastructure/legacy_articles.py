"""Compatibility adapter; retains existing SQLite transactions and revision history."""

from studio import profiles
from studio.db import now
from studio.infrastructure.article_cleanup import ArticleCleanup
from studio.service import Problem


class LegacyArticleRepository:
    def __init__(self, studio):
        self.studio = studio
        self.cleanup = ArticleCleanup(studio)

    def storage(self, clean=False):
        return self.cleanup.cleanup() if clean else self.cleanup.status()

    def listing(self, scope="active"):
        condition = {
            "active": "p.archived=0 AND t.post_id IS NULL",
            "archived": "p.archived=1 AND t.post_id IS NULL",
            "trash": "t.post_id IS NOT NULL",
        }[scope]
        return self.studio.db.rows(
            "SELECT p.id,p.title,p.summary,p.stage,p.version,p.updated_at,p.profile_id "
            "FROM posts p LEFT JOIN preview_article_trash t ON t.post_id=p.id WHERE "
            + condition + " ORDER BY p.updated_at DESC,p.rowid DESC"
        )

    def manage(self, article_id, action, version):
        if action == "purge":
            return self.cleanup.purge(article_id, version)
        with self.studio.db.connect(immediate=True) as db:
            post = db.execute("SELECT * FROM posts WHERE id=?", (article_id,)).fetchone()
            if not post:
                raise Problem("文章不存在", 404)
            if post["version"] != version:
                raise Problem("文章已有新版本，请刷新列表核对后再操作", 409)
            trash = db.execute("SELECT * FROM preview_article_trash WHERE post_id=?", (article_id,)).fetchone()
            if (action == "restore") != bool(trash):
                raise Problem("文章状态已变化，请刷新列表", 409)
            if action == "restore":
                db.execute("DELETE FROM preview_article_trash WHERE post_id=?", (article_id,))
                self.studio.archive(article_id, bool(trash["previous_archived"]), connection=db)
            else:
                archived = action != "unarchive"
                if action in ("archive", "unarchive") and bool(post["archived"]) == archived:
                    raise Problem("文章状态已变化，请刷新列表", 409)
                self.studio.archive(article_id, archived, connection=db)
                if action == "trash":
                    db.execute("INSERT INTO preview_article_trash VALUES(?,?,?)",
                               (article_id, post["archived"], now()))
        return {"ok": True, "post_id": article_id}

    def get(self, article_id):
        return self.studio.post(article_id)

    def save(self, values, article_id=None):
        return self.studio.save_post(values, article_id)

    def profiles(self):
        return [{"id": row["id"], "name": row["name"]} for row in profiles.listing(self.studio)]
