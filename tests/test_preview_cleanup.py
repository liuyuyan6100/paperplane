"""Permanent deletion uses isolated data and never exercises the installed library."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from studio import profiles
from studio.api.preview import create_app
from studio.db import now, uid
from studio.infrastructure.legacy_articles import LegacyArticleRepository
from studio.service import Problem, Studio


@pytest.fixture
def trash(tmp_path):
    app = Studio(tmp_path / "isolated")
    repo = LegacyArticleRepository(app)
    post = app.save_post({"title": "仅供永久删除回归", "body": "第一版合成正文"})
    yield app, repo, post
    app.close()


def asset(app, extension="png"):
    name = uid() + "." + extension
    folder = "videos" if extension == "mp4" else "media"
    path = app.db.data_dir / folder / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"synthetic-local-file" * 50)
    return path, ("/video/" if folder == "videos" else "/media/") + name


def test_purge_reclaims_only_exclusive_files_including_old_revisions_and_video(trash):
    app, repo, post = trash
    old, old_url = asset(app)
    video, video_url = asset(app, "mp4")
    shared, shared_url = asset(app)
    unrelated, _ = asset(app)
    external = app.db.data_dir.parent / "user-original.png"
    external.write_bytes(b"user-original")
    p = app.save_post({"version": 1, "cover": old_url, "body": video_url}, post["id"])
    p = app.save_post({"version": p["version"], "cover": shared_url, "body": "新版无视频"}, post["id"])
    other = app.save_post({"title": "共用封面的独立文章", "cover": shared_url, "body": "保留"})
    before = app.post(other["id"])
    bytes_to_free = old.stat().st_size + video.stat().st_size
    repo.manage(post["id"], "trash", p["version"])
    result = repo.manage(post["id"], "purge", p["version"] + 1)
    assert result["deleted_files"] == 2 and result["reclaimed_bytes"] == bytes_to_free
    assert result["protected_files"] == 1 and result["pending_files"] == 0
    assert result["database_compacted"]
    assert not old.exists() and not video.exists()
    assert shared.exists() and unrelated.exists() and external.read_bytes() == b"user-original"
    assert app.post(other["id"]) == before
    assert not app.db.rows("SELECT * FROM revisions WHERE post_id=?", (post["id"],))
    assert not app.db.rows("PRAGMA foreign_key_check")


def test_profile_and_shared_chat_references_survive_article_purge(trash):
    app, repo, post = trash
    file, url = asset(app)
    profile = profiles.save(app, {"name": "独立画像", "visual_style": "合成", "visual_references": [url]})
    p = app.save_post({"version": 1, "cover": url}, post["id"])
    stamp = now()
    with app.db.connect() as db:
        db.execute("INSERT INTO conversations(id,title,profile_id,created_at,updated_at) VALUES(?,?,?,?,?)", (uid(), "共享对话 " + url, profile["id"], stamp, stamp))
    saved_profile = app.db.rows("SELECT * FROM profiles")
    saved_chat = app.db.rows("SELECT * FROM conversations")
    repo.manage(post["id"], "trash", p["version"])
    result = repo.manage(post["id"], "purge", p["version"] + 1)
    assert file.exists() and result["protected_files"] == 1
    assert app.db.rows("SELECT * FROM profiles") == saved_profile
    assert app.db.rows("SELECT * FROM conversations") == saved_chat


def test_purge_deletes_owned_workflow_events_assistant_and_links_but_retains_derived_post(trash):
    app, repo, post = trash
    workflow, turn, conversation = uid(), uid(), uid()
    derived = app.save_post({"title": "独立修改的派生稿", "body": "必须保留"})
    with app.db.connect() as db:
        db.execute("UPDATE posts SET source_post_id=? WHERE id=?", (post["id"], derived["id"]))
        db.execute("INSERT INTO workflows(id,post_id,post_title,post_version,reference_ids,instruction,snapshot,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'review','review',?,?)",
                   (workflow, post["id"], post["title"], 1, "[]", "合成要求", "{}", now(), now()))
        db.execute("INSERT INTO workflow_events(workflow_id,at,status,stage,message) VALUES(?,?,'running','body','历史执行事件不能当作当前仍运行')", (workflow, now()))
        db.execute("INSERT INTO editor_assistant_turns(id,kind,target_id,message,mode,status,snapshot,created_at,updated_at) VALUES(?,'outline',?,'合成','discuss','review','{}',?,?)", (turn, workflow, now(), now()))
        db.execute("INSERT INTO conversations(id,title,profile_id,created_at,updated_at) VALUES(?,?,?,?,?)", (conversation, "共享对话", None, now(), now()))
        db.execute("INSERT INTO conversation_task_links VALUES(?,'workflow',?)", (conversation, workflow))
        db.execute("INSERT INTO model_calls(id,owner_kind,owner_id,task,status,attempt,started_at) VALUES(?,'workflows',?,'body','succeeded',1,?)", (uid(), workflow, now()))
    repo.manage(post["id"], "trash", 1)
    repo.manage(post["id"], "purge", 2)
    for table in ("workflows", "workflow_events", "editor_assistant_turns", "conversation_task_links", "model_calls"):
        assert not app.db.rows("SELECT * FROM " + table), table
    assert app.post(derived["id"])["body"] == "必须保留"
    assert app.post(derived["id"])["source_post_id"] is None
    assert len(app.db.rows("SELECT * FROM conversations")) == 1
    assert not app.db.rows("PRAGMA foreign_key_check")


@pytest.mark.parametrize("status", ["queued", "running", "uncertain"])
def test_running_or_unknown_task_blocks_purge_without_deleting_anything(trash, status):
    app, repo, post = trash
    file, url = asset(app)
    app.save_post({"version": 1, "cover": url}, post["id"])
    repo.manage(post["id"], "trash", 2)
    with app.db.connect() as db:
        db.execute("INSERT INTO article_videos(id,post_id,title,description,filename,sha256,bytes,status,created_at,updated_at) VALUES(?,?,?,'',?,'synthetic',0,?,?,?)",
                   (uid(), post["id"], "合成上传任务", uid() + ".mp4", status, now(), now()))
    with pytest.raises(Problem, match="执行中或结果待核对"):
        repo.manage(post["id"], "purge", 3)
    assert app.post(post["id"])["version"] == 3 and file.exists()
    assert not app.db.rows("SELECT * FROM preview_file_cleanup")


def test_file_lock_retry_rechecks_new_references_and_never_deletes_shared_file(trash):
    app, repo, post = trash
    file, url = asset(app)
    app.save_post({"version": 1, "cover": url}, post["id"])
    repo.manage(post["id"], "trash", 2)
    with patch.object(Path, "unlink", side_effect=PermissionError("synthetic lock")):
        result = repo.manage(post["id"], "purge", 3)
    assert result["pending_files"] == 1 and file.exists()
    app.save_post({"title": "后来采用同一图", "cover": url})
    result = repo.storage(True)
    assert result["protected_files"] == 1 and result["pending_files"] == 0 and file.exists()


def test_foreign_key_failure_rolls_back_before_file_cleanup(trash):
    app, repo, post = trash
    file, url = asset(app)
    app.save_post({"version": 1, "cover": url}, post["id"])
    repo.manage(post["id"], "trash", 2)
    with app.db.connect() as db:
        db.execute("CREATE TABLE future_reference(id TEXT PRIMARY KEY, original TEXT REFERENCES posts(id) ON DELETE RESTRICT)")
        db.execute("INSERT INTO future_reference VALUES('kept',?)", (post["id"],))
    # A reference outside the explicit ownership graph must fail closed.
    with patch("studio.infrastructure.article_cleanup.owned_rows", return_value={"posts": {1}}):
        with pytest.raises(Problem, match="关联记录"):
            repo.manage(post["id"], "purge", 3)
    assert app.post(post["id"])["version"] == 3 and file.exists()
    assert not app.db.rows("SELECT * FROM preview_file_cleanup")


def test_api_requires_trash_current_version_session_and_serializes_duplicate_purge(trash):
    app, repo, post = trash
    with TestClient(create_app(app, "http://127.0.0.1:1", "test", 43111), base_url="http://127.0.0.1:43111") as client:
        route = f"/api/v2/articles/{post['id']}/manage"
        assert client.post(route, json={"action": "purge", "version": 1}).status_code == 403
        client.headers["X-Studio-Token"] = "test"
        assert client.post(route, json={"action": "purge", "version": 1}).status_code == 409
        assert client.post(route, json={"action": "trash", "version": 1}).status_code == 200
        assert client.post(route, json={"action": "purge", "version": 1}).status_code == 409
        assert client.get("/api/v2/articles/trash-storage").json() == {"pending_files": 0}
        assert client.post("/api/v2/articles/trash-storage/cleanup", json={}).status_code == 200
    def purge(_):
        try:
            repo.manage(post["id"], "purge", 2)
            return True
        except Problem:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(purge, range(2))) == 1
    assert not app.db.rows("PRAGMA foreign_key_check")
