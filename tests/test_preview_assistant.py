"""M2 boundary, configuration import and actual browser regression with synthetic data."""

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from studio import profiles
from studio.config import Config
from studio.db import dumps, uid
from studio.infrastructure.configuration_reuse import finish_configuration, prepare_configuration
from studio.preview_runtime import PreviewService
from studio.service import Studio

ROOT = Path(__file__).resolve().parents[1]


def test_reuse_encrypted_configuration_is_read_only_and_preserves_preview(tmp_path):
    source, target = tmp_path / "original/local", tmp_path / "preview/local"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    original = Studio(source)
    original.config.update(
        {
            "AI_MODE": "api",
            "AI_BASE_URL": "https://example.com/v1",
            "AI_API_KEY": "synthetic-only-key",
            "AI_MODEL": "synthetic-model",
            "WECHAT_APP_SECRET": "synthetic-wechat",
            "UNRELATED": "excluded",
        }
    )
    original.save_post({"title": "不可复制的原稿", "body": "原始正文"})
    with original.db.connect(immediate=True) as db:
        db.execute(
            "UPDATE channels SET config=? WHERE id=?",
            (dumps({"editor_url": "https://example.com/editor"}), "csdn"),
        )
    original.close()
    accounts = source.parent / "platform-accounts.json"
    accounts.write_text(dumps({"csdn": {"id": "synthetic", "name": "合成账号"}}), encoding="utf-8")
    baseline = (source / "private.json").read_bytes()
    Config(target).update({"AI_MODEL": "preview-custom"})
    receipt = prepare_configuration(target, source)
    assert receipt["imported_fields"] == 4
    assert Config(target).get("AI_MODEL") == "preview-custom"
    assert Config(target).get("AI_API_KEY") == "synthetic-only-key"
    assert not Config(target).get("UNRELATED")
    preview = Studio(target)
    try:
        done = finish_configuration(preview, source)
        assert done["complete"] and done["browser_session"] == "requires_verification"
        assert preview.db.one("SELECT COUNT(*) AS n FROM posts")["n"] == 0
        assert (
            json.loads(preview.db.one("SELECT config FROM channels WHERE id='csdn'")["config"])[
                "editor_url"
            ]
            == "https://example.com/editor"
        )
        assert prepare_configuration(target, source) == done
        assert (source / "private.json").read_bytes() == baseline
        assert (target / "platform-accounts.json").read_bytes() == accounts.read_bytes()
        assert (
            "synthetic-only-key" not in (target / "private.json").read_text()
            if os.name == "nt"
            else True
        )
        assert "synthetic-only-key" not in dumps(done)
    finally:
        preview.close()


def test_import_preserves_custom_channel_and_rejects_overlap(tmp_path):
    source, target = tmp_path / "original", tmp_path / "preview"
    source.mkdir()
    target.mkdir()
    Config(source).update({"AI_MODE": "api"})
    prepare_configuration(target, source)
    app = Studio(target)
    try:
        with app.db.connect(immediate=True) as db:
            db.execute("UPDATE channels SET config=? WHERE id='csdn'", (dumps({"custom": "keep"}),))
        finish_configuration(app, source)
        assert json.loads(app.db.one("SELECT config FROM channels WHERE id='csdn'")["config"]) == {
            "custom": "keep"
        }
    finally:
        app.close()
    with pytest.raises(ValueError):
        prepare_configuration(source / "nested", source)


def test_completed_m2_import_adds_profile_style_and_reference_without_articles(tmp_path):
    source, target = tmp_path / "original", tmp_path / "preview"
    original = Studio(source)
    url = "/media/" + "b" * 32 + ".png"
    (source / "media").mkdir(exist_ok=True)
    (source / url.lstrip("/")).write_bytes(b"synthetic-style-reference")
    saved = profiles.save(
        original, {"name": "合成画像", "visual_style": "合成水彩风格", "visual_references": [url]}
    )
    baseline = original.db.one("SELECT * FROM profiles WHERE id=?", (saved["id"],))
    original.close()
    app = Studio(target)
    (target / "configuration-reuse.json").write_text(
        dumps({"complete": True, "private_done": True, "status": "imported"}), encoding="utf8"
    )
    try:
        receipt = finish_configuration(app, source)
        assert receipt["profiles_imported"] == 1 and receipt["profiles_done"]
        imported = profiles.get(app, saved["id"])
        assert imported["visual_style"] == saved["visual_style"]
        assert imported["visual_references"] == [url]
        assert (target / url.lstrip("/")).read_bytes() == b"synthetic-style-reference"
        assert app.db.one("SELECT COUNT(*) AS n FROM posts")["n"] == 0
        profiles.save(app, {**imported, "visual_style": "验证版修改后保留"}, saved["id"])
        assert finish_configuration(app, source) == receipt
        assert profiles.get(app, saved["id"])["visual_style"] == "验证版修改后保留"
        with sqlite3.connect((source / "studio.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            assert (
                dict(db.execute("SELECT * FROM profiles WHERE id=?", (saved["id"],)).fetchone())
                == baseline
            )
    finally:
        app.close()


def test_profile_style_import_preserves_existing_profile_name(tmp_path):
    source, target = tmp_path / "original", tmp_path / "preview"
    original = Studio(source)
    profiles.save(original, {"name": "已有名称", "visual_style": "原机风格"})
    original.close()
    app = Studio(target)
    kept = profiles.save(app, {"name": "已有名称", "visual_style": "验证版独立风格"})
    (target / "configuration-reuse.json").write_text(dumps({"complete": True}), encoding="utf8")
    try:
        receipt = finish_configuration(app, source)
        assert receipt["profiles_preserved"] == 1
        assert profiles.get(app, kept["id"])["visual_style"] == "验证版独立风格"
    finally:
        app.close()


@pytest.fixture(scope="module")
def assistant(tmp_path_factory):
    service = PreviewService(tmp_path_factory.mktemp("m2-assistant") / "isolated")

    def configure(app):
        app.config.update(
            {
                "AI_MODE": "api",
                "AI_MODEL": "synthetic-model",
                "AI_BASE_URL": "http://127.0.0.1:1/v1",
            }
        )

        def generate(post, *args, **kwargs):
            from studio.adapters import DeliveryError

            payload = json.loads(post["body"])
            if "模拟失败" in payload["message"]:
                raise DeliveryError("合成失败，原稿保留")
            time.sleep(0.1)
            selection = payload["selection"]
            replacement = (
                "片段已改😀"
                if selection
                else {**payload["draft"], "body": payload["draft"]["body"] + "\nAI建议"}
            )
            return dumps(
                {
                    "reply": "合成讨论回复" if payload["mode"] == "discuss" else "合成修改回复",
                    "replacement": replacement,
                }
            )

        app.generate_draft = generate
        profiles.save(app, {"name": "合成风格画像", "visual_style": "合成测试水彩风格"})

    service.configure = configure
    service.start()
    yield service
    service.stop()


def test_typed_assistant_contract_ownership_and_adoption(assistant):
    with httpx.Client(base_url=assistant.origin, trust_env=False) as client:
        client.headers["X-Studio-Token"] = client.get("/api/v2/session").json()["token"]
        article = client.post("/api/v2/articles", json={"title": "合成稿", "body": "原稿😀"}).json()
        other = client.post("/api/v2/articles", json={"title": "别的稿"}).json()
        route = f"/api/v2/articles/{article['id']}/assistant"
        source = client.get(route).json()
        turn_id = uid()
        values = {
            "request_id": turn_id,
            "revision": source["revision"],
            "mode": "edit",
            "message": "保留原意",
            "draft": source["content"],
        }
        assert (
            client.post(route, json=values, headers={"X-Studio-Token": "wrong"}).status_code == 403
        )
        assert client.post(route, json={**values, "mode": "tool"}).status_code == 422
        assert client.post(route, json=values).status_code == 200
        assert client.post(route, json=values).status_code == 200
        deadline = time.monotonic() + 10
        while (turn := client.get(route).json()["turns"][-1])["status"] in ("queued", "running"):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert turn["status"] == "review"
        assert (
            client.post(
                f"/api/v2/articles/{other['id']}/assistant/{turn_id}/accept",
                json={"draft": source["content"]},
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{route}/{turn_id}/accept", json={"draft": {**source["content"], "body": "后改"}}
            ).status_code
            == 409
        )
        assert (
            client.post(f"{route}/{turn_id}/accept", json={"draft": source["content"]}).json()[
                "replacement"
            ]["body"]
            == "原稿😀\nAI建议"
        )
        assert assistant.app.post(article["id"])["body"] == "原稿😀"
        assert not assistant.app.jobs()
        public = client.get("/api/v2/assistant/configuration").json()
        assert public["configured"] and "AI_BASE_URL" not in dumps(public)


def test_models_settings_and_per_request_selection(assistant):
    with httpx.Client(base_url=assistant.origin, trust_env=False) as client:
        client.headers["X-Studio-Token"] = client.get("/api/v2/session").json()["token"]
        assistant.app.config.update(
            {"AI_API_KEY": "synthetic-saved-secret", "IMAGE_API_KEY": "synthetic-image-secret"}
        )
        with patch(
            "studio.models.available",
            return_value={"models": ["synthetic-model", "synthetic-alternate"]},
        ):
            assert client.get("/api/v2/assistant/models").json()["models"] == [
                "synthetic-model",
                "synthetic-alternate",
            ]
            assert client.get("/api/v2/assistant/models?kind=image").status_code == 200
        assert client.get("/api/v2/assistant/models?kind=unknown").status_code == 422
        saved = client.post(
            "/api/v2/assistant/settings",
            json={"text_models": {"chat": "synthetic-chat"}, "image": {"model": "synthetic-image"}},
        )
        assert saved.status_code == 200
        assert (
            "synthetic-saved-secret" not in saved.text
            and "synthetic-image-secret" not in saved.text
        )
        assert assistant.app.config.get("AI_API_KEY") == "synthetic-saved-secret"
        assert assistant.app.config.get("IMAGE_API_KEY") == "synthetic-image-secret"
        assert (
            client.post(
                "/api/v2/assistant/settings",
                json={"connection": {"base_url": "https://example.com/v1"}},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v2/assistant/settings", json={"text_models": {"unknown": "bad"}}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v2/assistant/settings", json={}, headers={"X-Studio-Token": "wrong"}
            ).status_code
            == 403
        )
        article = client.post(
            "/api/v2/articles", json={"title": "模型选择合成稿", "body": "原始正文"}
        ).json()
        route = f"/api/v2/articles/{article['id']}/assistant"
        state = client.get(route).json()
        request_id = uid()
        payload = {
            "request_id": request_id,
            "revision": state["revision"],
            "mode": "discuss",
            "model": "synthetic-alternate",
            "message": "讨论表达",
            "draft": state["content"],
        }
        assert client.post(route, json=payload).status_code == 200
        assert client.post(route, json=payload).status_code == 200
        assert client.post(route, json={**payload, "model": "synthetic-other"}).status_code == 409
        assert (
            json.loads(
                assistant.app.db.one(
                    "SELECT route FROM editor_assistant_turns WHERE id=?", (request_id,)
                )["route"]
            )["model"]
            == "synthetic-alternate"
        )
        assert assistant.app.config.get("AI_CHAT_MODEL") == "synthetic-chat"


def test_assistant_real_browser(assistant, tmp_path):
    fixture = tmp_path / "browser.json"
    fixture.write_text(
        dumps({"url": assistant.origin, "output": str(ROOT / "test-results/m2-browser")}),
        encoding="utf-8",
    )
    with patch(
        "studio.models.available",
        return_value={"models": ["synthetic-model", "synthetic-alternate", "synthetic-image"]},
    ):
        result = subprocess.run(
            ["node", str(ROOT / "tests/verify_preview_assistant.cjs"), str(fixture)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    assert result.returncode == 0, result.stdout + result.stderr
