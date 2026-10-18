"""Metadata uses the existing persisted assistant without save/send side effects."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from studio import profiles
from studio.api.preview import create_app
from studio.article_metadata import FIELDS
from studio.db import dumps, uid
from studio.infrastructure.legacy_assistant import LegacyAssistant
from studio.infrastructure.legacy_tasks import LegacyTasks, prepare_preview_restart
from studio.service import Problem, Studio

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path):
    app = Studio(tmp_path)
    app.config.update(
        {"AI_MODE": "api", "AI_BASE_URL": "http://127.0.0.1:1/v1", "AI_MODEL": "synthetic-reused"}
    )
    profile = profiles.save(
        app, {"name": "合成作者 · 测试", "wechat_footer": "合成署名｜博客：https://example.com/"}
    )
    post = app.save_post({"title": "测试稿", "body": "保存的正文", "profile_id": profile["id"]})
    post = app.post(post["id"])
    app.generate_draft = lambda *a, **k: dumps(
        {"summary": "合成摘要，完全依据提供的正文。", "tags": ["测试", "回归"]}
    )
    try:
        yield app, post, LegacyAssistant(app)
    finally:
        app.close()


def send(port, post, draft=None, request_id=None):
    draft = draft or {key: post[key] for key in FIELDS}
    values = {
        "request_id": request_id or uid(),
        "revision": port.metadata(post["id"])["revision"],
        "draft": draft,
    }
    return port.suggest_metadata(post["id"], values), values


def test_unsaved_input_existing_profile_and_no_save_or_delivery(setup):
    app, post, port = setup
    draft = {key: post[key] for key in FIELDS}
    draft.update(
        body="最新的未保存正文",
        source="https://example.com/original",
        cover="/media/" + "a" * 32 + ".png",
    )
    history, values = send(port, post, draft)
    assert history["turns"][-1]["status"] == "queued"
    assert port.suggest_metadata(post["id"], values)["turns"][-1]["id"] == values["request_id"]
    with patch.object(app, "generate_draft", wraps=app.generate_draft) as model:
        assert app.editor_assistant.tick()
        captured = json.loads(model.call_args.args[0]["body"])
        assert captured["draft"]["body"] == draft["body"]
        assert model.call_args.args[0]["_model_route"]["model"] == "synthetic-reused"
    turn = port.metadata(post["id"])["turns"][-1]
    assert turn["status"] == "review"
    result = port.accept_metadata(post["id"], turn["id"], {"draft": draft})
    assert result["replacement"] == {
        "summary": "合成摘要，完全依据提供的正文。",
        "author": "合成署名",
        "tags": ["测试", "回归"],
    }
    assert "画像" in result["author_source"]
    assert app.post(post["id"]) == post
    assert not app.jobs()
    assert not app.db.rows("SELECT * FROM article_draft_intents")
    assert not app.db.rows("SELECT * FROM draft_preparations")
    assert not app.db.rows("SELECT * FROM profile_learning_events")
    tasks = LegacyTasks(app).listing(post_id=post["id"])
    task = tasks["tasks"][0]
    assert task["stage_label"] == "文章信息补齐" and not task["legacy_url"]
    assert "摘要" in task["instruction"]


@pytest.mark.parametrize(
    "summary,tags,author", [("人工摘要", ["人工标签"], "人工署名"), ("", [], "")]
)
def test_existing_information_is_preserved_and_author_not_invented(setup, summary, tags, author):
    app, post, port = setup
    draft = {key: post[key] for key in FIELDS}
    draft.update(summary=summary, tags=tags, author=author)
    send(port, post, draft)
    app.editor_assistant.tick()
    result = port.metadata(post["id"])["turns"][-1]["result"]["replacement"]
    assert result["summary"] == (summary or "合成摘要，完全依据提供的正文。")
    assert result["tags"] == (tags or ["测试", "回归"])
    assert result["author"] == (author or "合成署名")
    no_profile = app.save_post({"title": "无画像", "body": "没有署名依据"})
    send(port, no_profile)
    app.editor_assistant.tick()
    assert port.metadata(no_profile["id"])["turns"][-1]["result"]["replacement"]["author"] == ""


@pytest.mark.parametrize("change", ["draft", "saved", "profile"])
def test_changed_editor_saved_version_or_profile_blocks_adoption(setup, change):
    app, post, port = setup
    _, values = send(port, post)
    app.editor_assistant.tick()
    if change == "draft":
        values["draft"]["author"] = "后续手写署名"
    elif change == "saved":
        app.save_post({"version": post["version"], "body": "其他窗口保存"}, post["id"])
    else:
        profile = profiles.get(app, post["profile_id"])
        profiles.save(app, {**profile, "wechat_footer": "改过的署名"}, profile["id"])
    with pytest.raises(Problem, match="变化"):
        port.accept_metadata(post["id"], values["request_id"], {"draft": values["draft"]})


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        dumps({"summary": "有效摘要", "tags": ["x" * 30]}),
        dumps({"summary": "摘要", "tags": [], "body": "危险替换"}),
    ],
)
def test_invalid_model_output_fails_without_side_effects(setup, raw):
    app, post, port = setup
    send(port, post)
    app.generate_draft = lambda *a, **k: raw
    app.editor_assistant.tick()
    assert port.metadata(post["id"])["turns"][-1]["status"] == "failed"
    assert app.post(post["id"]) == post and not app.jobs()


def test_http_validation_ownership_restart_and_request_id(setup):
    app, post, port = setup
    asgi = create_app(app, "http://127.0.0.1:12346", "synthetic-token", 12345)
    with TestClient(asgi, base_url="http://127.0.0.1:12345") as client:
        url = "/api/v2/articles/" + post["id"] + "/metadata"
        draft = {key: post[key] for key in FIELDS}
        values = {
            "request_id": uid(),
            "revision": port.metadata(post["id"])["revision"],
            "draft": draft,
        }
        assert client.post(url, json=values).status_code == 403
        headers = {"X-Studio-Token": "synthetic-token"}
        invalid = {**values, "draft": {**draft, "extra": "unsupported"}}
        assert client.post(url, json=invalid, headers=headers).status_code == 422
        assert client.post(url, json=values, headers=headers).status_code == 200
        changed = {**values, "draft": {**draft, "body": "不同请求"}}
        assert client.post(url, json=changed, headers=headers).status_code == 409
        prepare_preview_restart(app)
        assert not app.editor_assistant.tick()
        assert port.metadata(post["id"])["turns"][-1]["status"] == "interrupted"
        app.editor_assistant.retry(values["request_id"])
        app.editor_assistant.tick()
        other = app.save_post({"title": "另一文章", "body": "正文"})
        wrong = "/api/v2/articles/" + other["id"] + "/metadata/" + values["request_id"] + "/accept"
        assert client.post(wrong, json={"draft": draft}, headers=headers).status_code == 404
        assert (
            client.post(
                url + "/" + values["request_id"] + "/accept", json={"draft": draft}, headers=headers
            ).status_code
            == 200
        )
    assert app.post(post["id"]) == post and not app.jobs()


def test_metadata_browser_review_and_current_article_tasks(tmp_path):
    from studio.preview_runtime import PreviewService

    seed = {}

    def configure(app):
        app.config.update(
            {"AI_MODE": "api", "AI_BASE_URL": "http://127.0.0.1:1/v1", "AI_MODEL": "synthetic"}
        )
        profile = profiles.save(
            app, {"name": "浏览器画像", "wechat_footer": "合成署名｜https://example.com/"}
        )
        post = app.save_post(
            {"title": "信息补齐测试", "body": "原始正文", "profile_id": profile["id"]}
        )
        seed["id"] = post["id"]
        app.generate_draft = lambda *a, **k: dumps(
            {"summary": "浏览器合成摘要，依据最新正文。", "tags": ["回归", "编辑"]}
        )

    service = PreviewService(tmp_path / "metadata-browser")
    service.configure = configure
    service.start()
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        dumps(
            {
                "url": service.origin,
                "output": str(ROOT / "test-results/m3.2-metadata-browser"),
                **seed,
            }
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["node", str(ROOT / "tests/verify_preview_metadata.cjs"), str(fixture)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not service.app.jobs()
        assert not service.app.db.rows("SELECT * FROM article_draft_intents")
    finally:
        service.stop()
