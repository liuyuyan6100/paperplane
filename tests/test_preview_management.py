"""Reversible article management and local close preference contracts."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.api.preview import create_app
from studio.infrastructure.legacy_articles import LegacyArticleRepository
from studio.preview_preferences import PreviewPreferences
from studio.preview_runtime import PreviewService
from studio.service import Problem, Studio

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def management(tmp_path):
    app = Studio(tmp_path / "isolated")
    post = app.save_post({"title": "管理原稿", "body": "正文和修订必须保留😀"})
    with TestClient(create_app(app, "http://127.0.0.1:1", "test", 43111),
                    base_url="http://127.0.0.1:43111") as client:
        client.headers["X-Studio-Token"] = "test"
        yield app, post, client
    app.close()


def test_archive_trash_restore_preserve_content_history_and_original_scope(management):
    app, post, client = management
    route = f"/api/v2/articles/{post['id']}/manage"
    revision = app.db.rows("SELECT * FROM revisions")
    media = app.db.data_dir / "media" / "synthetic.txt"
    media.parent.mkdir(exist_ok=True)
    media.write_text("unchanged")
    for action, version, scope in [("archive", 1, "archived"), ("trash", 2, "trash"),
                                   ("restore", 3, "archived"), ("unarchive", 4, "active")]:
        response = client.post(route, json={"action": action, "version": version})
        assert response.status_code == 200, response.text
        assert client.get("/api/v2/articles?scope=" + scope).json()["articles"][0]["id"] == post["id"]
        assert app.post(post["id"])["body"] == post["body"]
        assert app.db.rows("SELECT * FROM revisions") == revision
        assert media.read_text() == "unchanged" and not app.jobs()
    assert app.post(post["id"])["version"] == 5


def test_trash_blocks_legacy_save_unarchive_and_stale_actions(management):
    app, post, client = management
    route = f"/api/v2/articles/{post['id']}/manage"
    assert client.post(route, json={"action": "trash", "version": 1}).status_code == 200
    assert client.get("/api/v2/articles").json()["articles"] == []
    assert client.post(route, json={"action": "restore", "version": 1}).status_code == 409
    with pytest.raises(Problem):
        app.save_post({"version": 2, "title": "不能覆盖"}, post["id"])
    with pytest.raises(Problem):
        app.archive(post["id"], False)
    assert client.post(route, json={"action": "restore", "version": 2}).status_code == 200
    assert client.get("/api/v2/articles").json()["articles"][0]["id"] == post["id"]


def test_competing_manage_actions_only_one_can_change_same_version(management):
    app, post, _ = management
    def change(action):
        try:
            LegacyArticleRepository(app).manage(post["id"], action, 1)
            return True
        except Problem:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(change, ["archive", "trash"])) == 1
    assert app.post(post["id"])["version"] == 2


def test_pending_delivery_blocks_trash_without_partial_changes(management):
    app, post, client = management
    saved = app.save_post({"version": 1, "stage": "ready", "summary": "摘要"}, post["id"])
    app.enqueue(post["id"], {"version": saved["version"], "channels": ["local"], "ack_warnings": True})
    response = client.post(f"/api/v2/articles/{post['id']}/manage", json={"action": "trash", "version": 2})
    assert response.status_code == 400
    assert not app.db.rows("SELECT * FROM preview_article_trash")
    assert app.post(post["id"])["version"] == 2 and not app.post(post["id"])["archived"]


def test_preferences_and_management_validate_and_require_session(management):
    app, post, client = management
    assert client.get("/api/v2/preferences").json() == {"close_behavior": "ask"}
    assert client.post("/api/v2/preferences", json={"close_behavior": "exit"}).status_code == 200
    assert PreviewPreferences(app.db.data_dir).get()["close_behavior"] == "exit"
    for data in [{"close_behavior": "invalid"}, {"close_behavior": "tray", "autostart": True}]:
        assert client.post("/api/v2/preferences", json=data).status_code == 422
    client.headers.pop("X-Studio-Token")
    assert client.post("/api/v2/preferences", json={"close_behavior": "tray"}).status_code == 403
    assert client.post(f"/api/v2/articles/{post['id']}/manage", json={"action": "trash", "version": 1}).status_code == 403


def test_corrupt_preferences_are_preserved(tmp_path):
    settings = PreviewPreferences(tmp_path)
    settings.path.write_text("broken", encoding="utf-8")
    with pytest.raises(Problem):
        settings.update("exit")
    assert settings.path.read_text() == "broken"


def test_existing_database_adds_empty_trash_without_changing_posts_or_history(tmp_path):
    path = tmp_path / "upgrade"
    original = Studio(path)
    original.save_post({"title": "原版本文章", "body": "完整保留"})
    posts = original.db.rows("SELECT * FROM posts")
    revisions = original.db.rows("SELECT * FROM revisions")
    original.close()
    with original.db.connect() as db:
        db.execute("DROP TABLE preview_article_trash")
    upgraded = Studio(path)
    try:
        assert upgraded.db.rows("SELECT * FROM posts") == posts
        assert upgraded.db.rows("SELECT * FROM revisions") == revisions
        assert upgraded.db.rows("SELECT * FROM preview_article_trash") == []
    finally:
        upgraded.close()


def test_browser_preferences_and_reversible_article_management(tmp_path):
    service = PreviewService(tmp_path / "browser")
    def configure(app):
        app.save_post({"title": "管理测试稿", "body": "已保存的合成正文😀"})
    service.configure = configure
    service.start()
    fixture = tmp_path / "management.json"
    fixture.write_text(json.dumps({"url": service.origin, "output": str(ROOT / "test-results/m4.1.2-browser")}), encoding="utf-8")
    try:
        result = subprocess.run(["node", str(ROOT / "tests/verify_preview_management.cjs"), str(fixture)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not service.app.jobs() and not service.app.db.rows("SELECT * FROM model_calls")
    finally:
        service.stop()
