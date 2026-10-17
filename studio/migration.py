"""Import historical local exports; never contact WorkBuddy or execute old tasks."""
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from .db import decode_post, dumps, event, now, uid
from .markdown import safe_url


def latest_legacy(root):
    files = list((Path(root) / "backups").glob("*/cm-backup-*.json"))
    return sorted(files, key=lambda p: p.name, reverse=True)[0] if files else None


def inspect_legacy(root):
    path = latest_legacy(root)
    if not path:
        return {"available": False}
    data = json.loads(path.read_text(encoding="utf-8-sig")).get("data", {})
    return {"available": True, "name": path.name, "ideas": len(data.get("ideas", [])), "publish": len(data.get("publish", []))}


def flat(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(flat(v) for v in value)
    if isinstance(value, dict):
        for key in ("link", "text", "value", "date"):
            if key in value:
                return flat(value[key])
        return ""
    return str(value)


def import_legacy(app, root):
    from .service import Problem
    path = latest_legacy(root)
    if not path:
        raise Problem("没有找到旧版备份")
    data = json.loads(path.read_text(encoding="utf-8-sig")).get("data", {})
    app.db.backup()
    added_posts = added_jobs = 0
    with app.db.connect(immediate=True) as db:
        def add_post(row, key, title, idea=False):
            nonlocal added_posts
            existing = db.execute("SELECT id FROM posts WHERE legacy_key=?", (key,)).fetchone()
            if existing:
                return existing[0]
            tags = row.get("标签", [])
            if not isinstance(tags, list):
                tags = [flat(tags)] if tags else []
            tags = [flat(t)[:30] for t in tags if flat(t)][:12]
            source = flat(row.get("来源引用", ""))
            note = flat(row.get("备注", ""))
            stage = "idea" if idea and flat(row.get("状态")) in ("素材", "已选题") else "draft"
            body = note if idea else ""
            post_id = uid()
            db.execute("INSERT INTO posts(id,title,body,summary,tags,source,stage,created_at,updated_at,legacy_key) VALUES(?,?,?,?,?,?,?,?,?,?)", (post_id, title[:150], body, "", dumps(tags), (source or "旧版备份 · " + path.name)[:2000], stage, now(), now(), key))
            if not idea:
                db.execute("UPDATE posts SET archived=1 WHERE id=?", (post_id,))
            post = decode_post(db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())
            db.execute("INSERT INTO revisions(post_id,version,snapshot,created_at) VALUES(?,1,?,?)", (post_id, dumps(post), now()))
            added_posts += 1
            return post_id

        for index, row in enumerate(data.get("ideas", [])):
            title = flat(row.get("标题", "")).strip()
            if not title:
                continue
            add_post(row, "idea:" + str(row.get("record_id") or row.get("_id") or hashlib.sha256(title.encode()).hexdigest()), title, True)
        for index, row in enumerate(data.get("publish", [])):
            title, cid = flat(row.get("文章标题", "")).strip(), flat(row.get("平台", ""))
            if not title or not cid:
                continue
            key = "publish:" + str(row.get("record_id") or row.get("_id") or hashlib.sha256(dumps(row).encode()).hexdigest())
            if db.execute("SELECT 1 FROM jobs WHERE legacy_key=? UNION ALL SELECT 1 FROM delivery_tombstones WHERE legacy_key=? LIMIT 1", (key, key)).fetchone():
                continue
            if not db.execute("SELECT 1 FROM channels WHERE id=?", (cid,)).fetchone():
                # Retain historical unknown channels, disabled until explicitly configured.
                if not __import__('re').fullmatch(r"[a-z0-9_-]{1,30}", cid):
                    continue
                db.execute("INSERT INTO channels(id,name,kind,enabled) VALUES(?,?,'manual',0)", (cid, cid + "（旧版）"))
            post_id = add_post(row, "article:" + hashlib.sha256(title.encode()).hexdigest(), title)
            post = decode_post(db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone())
            url = safe_url(flat(row.get("发布链接", row.get("链接", ""))))
            old_state = flat(row.get("状态", ""))
            # Historical failures or pending items are inert, never a runnable queue.
            status = "published" if old_state == "已发布" and url else "canceled"
            message = "从旧版备份导入；正文未包含在备份内，请另行导入 Markdown。原状态：" + old_state
            external_id = flat(row.get("外部ID", ""))
            job_id = uid()
            metrics = next((m for m in data.get("stats", []) if flat(m.get("平台")) == cid and ((external_id and flat(m.get("外部ID")) == external_id) or flat(m.get("文章标题")) == title)), {})

            def number(key):
                try:
                    return max(0, int(flat(metrics.get(key)) or 0))
                except (TypeError, ValueError):
                    return 0

            # Distinguish historical rows on the same channel from new source revisions.
            legacy_version = -(index + 1)
            post["version"] = legacy_version
            due = flat(row.get("实际发布时间") or row.get("计划日期")) or now()
            db.execute("INSERT INTO jobs(id,run_id,post_id,channel_id,version,snapshot,status,due_at,created_at,updated_at,url,external_id,note,reads,likes,comments,metrics_at,legacy_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, "legacy", post_id, cid, legacy_version, dumps(post), status, due, now(), now(), url, external_id, message, number("阅读"), number("点赞"), number("评论"), (flat(metrics.get("采集时间")) or now()) if metrics else None, key))
            event(db, job_id, status, message)
            added_jobs += 1
    return {"posts": added_posts, "jobs": added_jobs, "source": path.name}


def backup_bundle(app):
    snapshot = app.db.backup()
    out = io.BytesIO()
    hashes = {}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        files = [(snapshot, "studio.sqlite3")]
        folder = app.db.data_dir / "media"
        if folder.exists():
            files.extend((p, "media/" + p.name) for p in sorted(folder.iterdir()) if p.is_file())
        folder=app.db.data_dir/'videos'
        if folder.exists():
            files.extend((p,'videos/'+p.name) for p in sorted(folder.iterdir()) if p.is_file() and not p.is_symlink() and re.fullmatch(r'[a-f0-9]{32}\.mp4',p.name))
        for path, name in files:
            data = path.read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            archive.writestr(name, data)
        archive.writestr("manifest.json", dumps({"app": "paperplane-studio", "version": 1, "created_at": now(), "files": hashes}))
    return out.getvalue()
