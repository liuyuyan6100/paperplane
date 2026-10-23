"""Synthetic draft handoff: no external model or platform calls."""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from studio.api.preview import create_app
from studio.db import dumps
from studio.preview_runtime import PreviewService
from studio.service import Studio

ROOT = Path(__file__).resolve().parents[1]


def configure(app):
    app.config.update({"WECHAT_APP_ID": "synthetic", "WECHAT_APP_SECRET": "synthetic-secret"})
    with app.db.connect() as db:
        db.execute("UPDATE channels SET enabled=1,config=? WHERE id='wechat'", (dumps({"mode": "api"}),))


def article(app, **values):
    return app.save_post({"title": "合成草稿衔接测试", "body": "完整的合成正文。", "summary": "合成摘要", "author": "合成作者", **values})


@pytest.fixture
def ctx(tmp_path):
    app = Studio(tmp_path / "drafts")
    configure(app)
    with patch("studio.wechat.validate_post", return_value=None), TestClient(
        create_app(app, "http://127.0.0.1:1", "synthetic", 43111), base_url="http://127.0.0.1:43111"
    ) as client:
        client.headers["X-Studio-Token"] = "synthetic"
        yield app, client
    app.close()


def route(post):
    return f"/api/v2/articles/{post['id']}/draft"


def test_view_is_read_only_and_uses_delivery_checks(ctx):
    app, client = ctx
    post = article(app, summary="", body="[待核实] 合成内容")
    before = app.post(post["id"])
    for _ in range(2):
        response = client.get(route(post))
        assert response.status_code == 200, response.text
        value = response.json()
        assert not value["report"]["ok"]
        assert any(c["label"] == "待核实内容" and c["level"] == "error" for c in value["report"]["checks"])
        assert value["intent"] is value["job"] is value["preparation"] is None
    assert app.post(post["id"]) == before
    assert not app.db.rows("SELECT * FROM jobs")
    assert not app.db.rows("SELECT * FROM draft_preparations")


def test_start_once_receipt_projection_and_unknown_never_resends(ctx):
    app, client = ctx
    post = article(app)
    started = client.post(route(post) + "/start", json={"version": post["version"]})
    assert started.status_code == 200, started.text
    value = started.json()
    assert value["job"]["status"] == "queued"
    assert value["post"]["stage"] == "ready"
    assert "snapshot" not in value["job"] and "synthetic-secret" not in started.text
    with app.db.connect() as db:
        db.execute("UPDATE jobs SET status='uncertain',external_id='synthetic-receipt' WHERE id=?", (value["job"]["id"],))
    for _ in range(2):
        response = client.post(route(post) + "/start", json={"version": value["post"]["version"]})
        assert response.json()["job"]["status"] == "uncertain"
        assert response.json()["job"]["external_id"] == "synthetic-receipt"
    assert len(app.db.rows("SELECT * FROM jobs")) == 1
    assert client.post(route(post) + "/cancel", json={"version": value["post"]["version"]}).status_code == 409


def test_waiting_save_resumes_and_cancel_stops(ctx):
    app, client = ctx
    for cancel in (False, True):
        post = article(app, summary="")
        value = client.post(route(post) + "/start", json={"version": post["version"]}).json()
        assert value["intent"]["status"] == "waiting"
        current = value["post"]
        if cancel:
            assert client.post(route(post) + "/cancel", json={"version": current["version"]}).status_code == 200
        app.save_post({"version": current["version"], "summary": "已手动补齐"}, post["id"])
        app.article_flow.resume_due()
        app.article_flow.resume_due()
        jobs = app.db.rows("SELECT * FROM jobs WHERE post_id=?", (post["id"],))
        assert len(jobs) == (0 if cancel else 1)


def test_version_auth_shape_and_cross_article_preparation(ctx):
    app, client = ctx
    post = article(app, summary="")
    assert client.post(route(post) + "/start", json={"version": True}).status_code == 422
    assert client.post(route(post) + "/publish", json={"version": 1}).status_code == 422
    assert client.post(route(post) + "/start", json={"version": 1}, headers={"X-Studio-Token": "wrong"}).status_code == 403
    app.save_post({"version": post["version"], "title": "更新标题"}, post["id"])
    assert client.post(route(post) + "/start", json={"version": post["version"]}).status_code == 409
    other = article(app, summary="")
    values = []
    for p in (app.post(post["id"]), other):
        values.append(client.post(route(p) + "/start", json={"version": p["version"]}).json())
    target, foreign = values
    assert client.post(route(post) + "/confirm", json={"version": target["post"]["version"], "preparation_id": foreign["preparation"]["id"]}).status_code == 409
    assert not app.db.rows("SELECT * FROM jobs")


def test_suggestion_review_confirm_and_stale_guard(ctx):
    app, client = ctx
    post = article(app, summary="")
    value = client.post(route(post) + "/start", json={"version": post["version"]}).json()
    with patch.object(app, "generate_draft", return_value='{"summary":"合成建议摘要"}'):
        app.preparation.tick()
    review = client.get(route(post)).json()
    assert review["preparation"]["status"] == "review"
    assert app.post(post["id"])["summary"] == ""
    # Missing cover remains a visible issue in the proposal; mocked local validation isolates handoff.
    response = client.post(route(post) + "/confirm", json={"version": review["post"]["version"], "preparation_id": review["preparation"]["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["post"]["summary"] == "合成建议摘要"
    assert response.json()["job"]["status"] == "queued"
    assert client.post(route(post) + "/confirm", json={"version": value["post"]["version"], "preparation_id": value["preparation"]["id"]}).status_code == 409


def test_archived_post_cannot_start(ctx):
    app, client = ctx
    post = article(app)
    app.archive(post["id"], True)
    current = app.post(post["id"])
    assert client.post(route(post) + "/start", json={"version": current["version"]}).status_code == 409


def test_draft_panel_browser(tmp_path):
    service = PreviewService(tmp_path / "browser")
    saved = {}
    def setup(app):
        configure(app)
        saved.update(article(app, body="[待核实] 浏览器合成内容"))
    service.configure = setup
    with patch("studio.wechat.validate_post", return_value=None):
        service.start()
        try:
            fixture = tmp_path / "drafts.json"
            fixture.write_text(json.dumps({"url": service.origin, "id": saved["id"], "screenshot": str(ROOT / "test-results/m4.4-drafts.png")}), encoding="utf-8")
            result = subprocess.run(["node", str(ROOT / "tests/verify_preview_drafts.cjs"), str(fixture)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            assert result.returncode == 0, result.stdout + result.stderr
            assert not service.app.db.rows("SELECT * FROM jobs")
            assert service.app.article_flow.get(saved["id"])["intent"]["status"] == "canceled"
        finally:
            service.stop()


def test_canceled_intent_cannot_restart_preparation_and_counts_as_active(ctx):
    app, client = ctx
    post = article(app, summary="")
    result = client.post(route(post) + "/start", json={"version": post["version"]}).json()
    service = PreviewService(app.db.data_dir)
    service.app = app
    assert service.active_tasks()
    version = result["post"]["version"]
    assert client.post(route(post) + "/cancel", json={"version": version}).status_code == 200
    assert not service.active_tasks()
    assert client.post(route(post) + "/prepare", json={"version": version}).status_code == 409
    assert len(app.db.rows("SELECT * FROM draft_preparations")) == 1


def test_tombstone_survives_projection_without_receipt_or_new_job(ctx):
    app, client = ctx
    post = article(app)
    from studio.db import now
    with app.db.connect() as db:
        db.execute("INSERT INTO delivery_tombstones(post_id,channel_id,version,job_id,status,purged_at) VALUES(?,?,?,?,?,?)", (post["id"], "wechat", post["version"], "c" * 32, "draft", now()))
    current = app.save_post({"version": post["version"], "summary": "新版摘要"}, post["id"])
    response = client.post(route(post) + "/start", json={"version": current["version"]})
    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "draft"
    assert "记录已清理" in response.json()["job"]["note"]
    assert response.json()["job"]["version"] != current["version"]
    assert not app.db.rows("SELECT * FROM jobs")
