"""M4.2 contracts and browser exercise use synthetic text, images and isolated data."""

import base64
import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import image_provider, profiles
from studio.adapters import DeliveryError
from studio.api.preview import create_app
from studio.db import uid
from studio.infrastructure.legacy_illustrations import LegacyIllustrations
from studio.preview_runtime import PreviewService
from studio.service import Studio

ROOT = Path(__file__).resolve().parents[1]


def png(color="teal"):
    stream = io.BytesIO()
    Image.new("RGB", (256, 192), color).save(stream, format="PNG")
    return stream.getvalue()


def configure(app):
    app.config.update({"AI_MODE": "api", "AI_MODEL": "synthetic-text"})
    image_provider.save(app.config, {"base_url": "https://image.example/v1",
                                   "api_key": "synthetic-private-image-key", "model": "synthetic-image", "references": "edits"})
    reference = app.upload({"data": base64.b64encode(png()).decode(), "title": "合成参考图"})["url"]
    profile = profiles.save(app, {"name": "合成测试画像", "visual_style": "温暖纸张与工程墨线", "visual_references": [reference]})
    return app.save_post({"title": "M4配图浏览器验收", "stage": "draft", "body": "## 实践方法\n\n明确范围、保存输入并核对结果。\n\n## 验证边界\n\n插画是概念解释，不充当真实截图。\n\n— 合成作者", "author": "合成作者", "profile_id": profile["id"], "source": "https://example.com/source", "cover": "https://example.com/old-cover.png"})


@pytest.fixture
def ctx(tmp_path):
    app = Studio(tmp_path / "images")
    post = configure(app)
    port = LegacyIllustrations(app)
    with TestClient(create_app(app, "http://127.0.0.1:1", "synthetic", 43111), base_url="http://127.0.0.1:43111") as client:
        client.headers["X-Studio-Token"] = "synthetic"
        yield app, post, port, client
    app.close()


def setup_plan(ctx):
    _, post, _, client = ctx
    base = f"/api/v2/articles/{post['id']}/illustrations"
    values = {"request_id": uid(), "post_version": post["version"]}
    plan = client.post(base, json=values).json()
    return base + "/" + plan["id"], plan, values


def original(ctx, url, plan, index=0):
    _, _, _, client = ctx
    asset = client.post("/api/v2/illustrations/upload", json={"title": "合成原图", "data": base64.b64encode(png("navy")).decode()}).json()
    values = {"request_id": uid(), "version": plan["version"], "page_index": index, "image_url": asset["url"]}
    result = client.post(url + "/import-original", json=values)
    assert result.status_code == 200, result.text
    return result.json(), values


def test_contract_snapshot_safe_projection_and_idempotent_creation(ctx):
    app, post, _, client = ctx
    url, plan, values = setup_plan(ctx)
    assert client.post(url.rsplit("/", 1)[0], json=values).json()["id"] == plan["id"]
    assert client.post(url.rsplit("/", 1)[0], json=values | {"post_version": 2}).status_code == 409
    assert plan["profile"]["visual_style"] == "温暖纸张与工程墨线" and len(plan["references"]) == 1
    assert [p["slot"] for p in plan["pages"]] == ["cover", "body"]
    assert "context" not in plan and "snapshot" not in plan and "run_token" not in plan
    assert "synthetic-private-image-key" not in json.dumps(plan)
    assert app.post(post["id"])["version"] == 1 and not app.jobs()
    assert client.get(url.rsplit("/", 1)[0]).json()["plans"][0]["id"] == plan["id"]


@pytest.mark.parametrize("index", [0, 1])
def test_preview_atomic_adoption_idempotence_and_old_revision(ctx, index):
    app, post, _, client = ctx
    url, plan, _ = setup_plan(ctx)
    plan, imported = original(ctx, url, plan, index)
    assert client.post(url + "/import-original", json=imported).status_code == 200
    candidate = plan["candidates"][0]
    values = {"candidate_id": candidate["id"], "post_version": 1, "placement": plan["pages"][index].get("placement", ""), "caption": "合成图片说明", "ack_changes": False}
    result = client.post(url + "/preview", json=values)
    assert result.status_code == 200 and app.post(post["id"])["version"] == 1
    result = client.post(url + "/adopt", json=values)
    assert result.status_code == 200, result.text
    saved = result.json()["post"]
    assert saved["version"] == 2 and saved["source"] == post["source"]
    assert saved["author"] == post["author"] and saved["profile_id"] == post["profile_id"]
    if index == 0:
        assert saved["cover"] == candidate["image_url"] and saved["body"] == post["body"]
    else:
        assert saved["cover"] == post["cover"]
        assert saved["body"].index(candidate["image_url"]) < saved["body"].index("— 合成作者")
    assert client.post(url + "/adopt", json=values).json()["already_adopted"]
    assert app.post(post["id"])["version"] == 2
    assert json.loads(app.db.one("SELECT snapshot FROM revisions WHERE post_id=? AND version=1", (post["id"],))["snapshot"])["body"] == post["body"]
    assert not app.jobs()


def test_generation_references_parent_failure_and_manual_recovery(ctx):
    app, post, _, client = ctx
    url, plan, _ = setup_plan(ctx)
    values = {"request_id": uid(), "version": 1, "page_index": 0, "instruction": "保留纸张质感"}
    assert client.post(url + "/generate", json=values).status_code == 200
    assert client.post(url + "/generate", json=values).status_code == 200
    assert len(app.db.rows("SELECT id FROM illustration_candidates")) == 1
    captured = []
    def generate(config, prompt, size, references):
        captured.append((prompt, references))
        return png()
    with patch("studio.image_provider.generate", side_effect=generate):
        assert app.illustrations.tick()
    assert len(captured[0][1]) == 1 and "温暖纸张与工程墨线" in captured[0][0]
    plan = client.get(url).json()
    assert "context" not in plan["candidates"][0] and "synthetic-private-image-key" not in json.dumps(plan)
    parent = plan["candidates"][0]["id"]
    assert client.post(url + "/generate", json=values | {"request_id": uid(), "parent_id": parent}).status_code == 200
    with patch("studio.image_provider.generate", side_effect=DeliveryError("合成图片失败，需人工核对")):
        assert app.illustrations.tick()
    assert not app.illustrations.tick()
    plan = client.get(url).json()
    assert [c["status"] for c in plan["candidates"]] == ["review", "failed"]
    assert app.post(post["id"])["cover"] == post["cover"]
    assert client.post(url + "/generate", json=values | {"request_id": uid()}).status_code == 200


def test_change_conflict_archive_and_cross_article_rejection(ctx):
    app, post, _, client = ctx
    url, plan, _ = setup_plan(ctx)
    plan, _ = original(ctx, url, plan)
    values = {"candidate_id": plan["candidates"][0]["id"], "post_version": 1}
    app.save_post({"version": 1, "body": "已保存的新手写稿"}, post["id"])
    assert client.post(url + "/adopt", json=values).status_code == 409
    assert client.post(url + "/adopt", json=values | {"post_version": 2}).status_code == 409
    assert client.post(url + "/adopt", json=values | {"post_version": 2, "ack_changes": True}).status_code == 200
    assert app.post(post["id"])["body"] == "已保存的新手写稿"
    other = app.save_post({"title": "另一篇", "body": "不同文章"})
    assert client.get(url.replace(post["id"], other["id"])).status_code == 404
    assert client.post(url.replace(post["id"], other["id"]) + "/adopt", json=values).status_code == 404
    assert client.post(url + "/archive", json={"version": 1, "archived": True}).status_code == 200
    assert client.post(url + "/adopt", json=values).status_code == 409
    assert client.post(url + "/archive", json={"version": 1, "archived": False}).status_code == 200


def test_atomic_failure_and_concurrent_adoption(ctx):
    app, post, port, client = ctx
    url, plan, _ = setup_plan(ctx)
    plan, _ = original(ctx, url, plan)
    values = {"candidate_id": plan["candidates"][0]["id"], "post_version": 1}
    with app.db.connect() as db:
        db.execute("CREATE TRIGGER abort_image_revision BEFORE INSERT ON revisions BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END")
    with pytest.raises(Exception, match="synthetic rollback"):
        port.change(post["id"], plan["id"], "adopt", values)
    assert app.post(post["id"])["version"] == 1 and not app.illustrations.get(plan["id"])["adopted"]
    with app.db.connect() as db:
        db.execute("DROP TRIGGER abort_image_revision")
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda _: port.change(post["id"], plan["id"], "adopt", values), range(2)))
    assert sum(r["already_adopted"] for r in results) == 1 and app.post(post["id"])["version"] == 2


@pytest.mark.parametrize("bad", [{"version": True}, {"version": "1"}, {"version": 1, "unexpected": "value"}])
def test_strict_inputs_and_security_boundary(ctx, bad):
    _, _, _, client = ctx
    url, _, _ = setup_plan(ctx)
    assert client.post(url + "/improve", json=bad).status_code == 422
    assert client.post(url + "/improve", json={"version": 1}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(url + "/improve", json={"version": 1}, headers={"X-Studio-Token": "wrong"}).status_code == 403
    assert client.post("/api/v2/illustrations/upload", json={"title": "无效图片", "data": base64.b64encode(b"not an image").decode()}).status_code == 400


def test_restart_marks_images_interrupted_without_model_calls(tmp_path):
    data = tmp_path / "restart"
    app = Studio(data)
    post = configure(app)
    plan = app.illustrations.create({"post_id": post["id"], "post_version": 1})
    app.illustrations.generate(plan["id"], {"request_id": uid(), "version": 1, "page_index": 0})
    app.close()
    other = Studio(data)
    try:
        assert other.illustrations.get(plan["id"])["candidates"][0]["status"] == "interrupted"
        assert not other.illustrations.tick() and other.post(post["id"])["version"] == 1
        assert not other.jobs()
    finally:
        other.close()


def test_real_browser_image_planning_adoption_and_unsaved_protection(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPLANE_USE_SKILLS", "0")
    seed = {}
    def setup(app):
        seed.update(configure(app))
    service = PreviewService(tmp_path / "browser")
    service.configure = setup
    fixture = tmp_path / "browser.json"
    with patch("studio.image_provider.generate", side_effect=lambda *a: png()):
        service.start()
        fixture.write_text(json.dumps({"url": service.origin, "post_id": seed["id"], "png": base64.b64encode(png()).decode(), "output": str(ROOT / "test-results/m4.2-browser")}), encoding="utf-8")
        try:
            result = subprocess.run(["node", str(ROOT / "tests/verify_preview_illustrations.cjs"), str(fixture)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            assert result.returncode == 0, result.stdout + result.stderr
            assert not service.app.jobs()
        finally:
            service.stop()
