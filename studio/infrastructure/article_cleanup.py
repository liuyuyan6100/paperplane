"""Explicit trash deletion; reclaim only local files no remaining record references."""

import re
import sqlite3

from studio.db import now
from studio.service import Problem

SCHEMA = """CREATE TABLE IF NOT EXISTS preview_file_cleanup (
 folder TEXT NOT NULL, filename TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(folder,filename))"""
ACTIVE = {"queued", "running", "pending", "uploading", "checking", "uncertain", "unknown"}
LOCAL = re.compile(r"(?<![a-zA-Z0-9:/])/(media|video)/([a-f0-9]{32}\.(?:png|jpg|webp|gif|mp4))")


def tables(db):
    return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            if re.fullmatch(r"[a-z][a-z0-9_]*", r[0])]


def inventory(db):
    return {table: {r["_row"]: dict(r) for r in db.execute(f'SELECT rowid AS _row,* FROM "{table}"')}
            for table in tables(db) if table != "preview_file_cleanup"}


def owned_rows(rows, post_id, db):
    removed = {table: set() for table in rows}
    for table, items in rows.items():
        for key, row in items.items():
            if (table != "posts" and row.get("post_id") == post_id
                    or table == "posts" and row.get("id") == post_id
                    or table == "repurpose_variants" and row.get("source_post_id") == post_id
                    or table == "topic_plans" and row.get("selected_post_id") == post_id):
                removed[table].add(key)
    edges = [(table, f["from"], f["table"], f["to"] or "id") for table in rows
             for f in db.execute(f'PRAGMA foreign_key_list("{table}")')
             if table != "posts" and (table, f["from"]) != ("repurpose_variants", "applied_post_id")]
    # Older modules have logical links without a declared SQLite foreign key.
    edges += [(table, column, parent, "id") for table in rows for column, parent in (
        ("job_id", "jobs"), ("workflow_id", "workflows"), ("plan_id", "illustration_plans"),
        ("candidate_id", "illustration_candidates"), ("variant_id", "repurpose_variants"),
        ("review_id", "performance_reviews")) if any(column in r for r in rows[table].values())]
    while True:
        before = sum(map(len, removed.values()))
        for child, column, parent, target in edges:
            values = {rows[parent][k][target] for k in removed.get(parent, ())}
            removed[child].update(k for k, r in rows[child].items() if r.get(column) in values)
        targets = {"post": {post_id}, "metadata": {post_id},
                   "outline": {rows["workflows"][k]["id"] for k in removed.get("workflows", ())},
                   "body": {rows["workflows"][k]["id"] for k in removed.get("workflows", ())},
                   "plan": {rows["illustration_plans"][k]["id"] for k in removed.get("illustration_plans", ())},
                   "image": {rows["illustration_candidates"][k]["id"] for k in removed.get("illustration_candidates", ())},
                   "variant": {rows["repurpose_variants"][k]["id"] for k in removed.get("repurpose_variants", ())}}
        for key, row in rows.get("editor_assistant_turns", {}).items():
            if row["target_id"] in targets.get(row["kind"], set()):
                removed["editor_assistant_turns"].add(key)
        if before == sum(map(len, removed.values())):
            break
    from studio.infrastructure.legacy_tasks import SOURCES

    for table in ("task_commands", "conversation_task_links"):
        for key, row in rows.get(table, {}).items():
            source = next((s for s in SOURCES if s.kind == row["kind"]), None)
            if source and any(rows[source.table][k][source.key] == row["task_id"] for k in removed.get(source.table, ())):
                removed[table].add(key)
    for key, row in rows.get("model_calls", {}).items():
        if any(rows[row["owner_kind"]][k].get("id") == row["owner_id"] for k in removed.get(row["owner_kind"], ())):
            removed["model_calls"].add(key)
    return removed


def referenced(rows, filename):
    return any(filename in value for table, items in rows.items() if table != "assets"
               for row in items.values() for value in row.values() if isinstance(value, str))


class ArticleCleanup:
    def __init__(self, app):
        self.app = app
        with app.db.connect() as db:
            db.execute(SCHEMA)

    def purge(self, post_id, version):
        from studio.infrastructure.legacy_tasks import SOURCES

        task_tables = {s.table for s in SOURCES} | {"article_videos", "task_commands"}
        try:
            with self.app.db.connect(immediate=True) as db:
                post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
                if not post:
                    raise Problem("文章不存在或已永久删除", 404)
                if post["version"] != version:
                    raise Problem("文章版本已变化，请重新核对回收站", 409)
                if not db.execute("SELECT 1 FROM preview_article_trash WHERE post_id=?", (post_id,)).fetchone():
                    raise Problem("只能永久删除回收站内的文章", 409)
                rows = inventory(db)
                removed = owned_rows(rows, post_id, db)
                selected = [rows[t][k] for t, keys in removed.items() for k in keys]
                if any(rows[t][k].get("status") in ACTIVE for t, keys in removed.items()
                       if t in task_tables for k in keys):
                    raise Problem("本篇仍有执行中或结果待核对的任务，处理完成后再永久删除", 409)
                files = set()
                for row in selected:
                    for value in row.values():
                        if isinstance(value, str):
                            files.update(("videos" if folder == "video" else folder, name)
                                         for folder, name in LOCAL.findall(value.replace("\\/", "/")))
                    name = row.get("filename", "")
                    if re.fullmatch(r"[a-f0-9]{32}\.mp4", name):
                        files.add(("videos", name))
                # Keep independently edited derived articles and proposals belonging to another source.
                db.execute("UPDATE posts SET source_post_id=NULL WHERE source_post_id=?", (post_id,))
                db.execute("UPDATE repurpose_variants SET applied_post_id=NULL WHERE applied_post_id=?", (post_id,))
                db.execute("PRAGMA defer_foreign_keys=ON")
                for table, keys in removed.items():
                    db.executemany(f'DELETE FROM "{table}" WHERE rowid=?', [(k,) for k in keys])
                for folder, filename in files:
                    db.execute("INSERT OR IGNORE INTO preview_file_cleanup VALUES(?,?,?)", (folder, filename, now()))
        except sqlite3.IntegrityError:
            raise Problem("关联记录尚不能安全删除，文章与文件保持，请重新核对", 409) from None
        return {"ok": True, "post_id": post_id, **self.cleanup()}

    def status(self):
        return {"pending_files": self.app.db.one("SELECT COUNT(*) n FROM preview_file_cleanup")["n"]}

    def cleanup(self):
        freed, deleted, protected = 0, 0, 0
        root = self.app.db.data_dir.resolve()
        with self.app.db.connect(immediate=True) as db:
            rows = inventory(db)
            for item in db.execute("SELECT * FROM preview_file_cleanup").fetchall():
                folder, filename = item["folder"], item["filename"]
                if referenced(rows, filename):
                    protected += 1
                    db.execute("DELETE FROM preview_file_cleanup WHERE folder=? AND filename=?", (folder, filename))
                    continue
                if folder not in ("media", "videos") or not re.fullmatch(r"[a-f0-9]{32}\.(png|jpg|gif|webp|mp4)", filename):
                    continue
                directory = root / folder
                path = directory / filename
                if directory.resolve().parent != root or path.is_symlink() or path.resolve().parent != directory.resolve():
                    continue
                try:
                    size = path.stat().st_size if path.is_file() else 0
                    path.unlink(missing_ok=True)
                except OSError:
                    continue  # Explicit deletion intent survives; another use of the file is rechecked on retry.
                db.execute("DELETE FROM assets WHERE url=?", ("/media/" + filename,))
                db.execute("DELETE FROM preview_file_cleanup WHERE folder=? AND filename=?", (folder, filename))
                freed += size
                deleted += int(size > 0)
        compacted = False
        try:
            with self.app.db.connect() as db:
                db.execute("PRAGMA busy_timeout=500")
                db.execute("VACUUM")
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                compacted = True
        except sqlite3.Error:
            pass  # Freed SQLite pages remain reusable; media cleanup is already committed.
        return {"reclaimed_bytes": freed, "deleted_files": deleted, "protected_files": protected,
                "database_compacted": compacted, **self.status()}
