"""The small editor updates core profile fields without losing legacy data."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import profiles
from studio.api.preview import create_app
from studio.preview_runtime import PreviewService
from studio.service import Studio

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ctx(tmp_path):
    app = Studio(tmp_path / "profile-preview")
    original = profiles.save(app, {
        "name": "合成画像",
        "direction": "原方向",
        "audience": "原读者",
        "goal": "原目标",
        "style": "保留的表达风格",
        "preferences": "保留的红线",
        "wechat_footer": "保留的公众号署名",
    })
    article = app.save_post({"title": "画像关联样稿", "profile_id": original["id"]})
    with TestClient(
        create_app(app, "http://127.0.0.1:1", "synthetic-token", 43111),
        base_url="http://127.0.0.1:43111",
    ) as client:
        client.headers["X-Studio-Token"] = "synthetic-token"
        yield app, original, article, client
    app.close()


def test_core_edit_preserves_other_fields_article_and_revision(ctx):
    app, original, article, client = ctx
    route = f"/api/v2/profiles/{original['id']}"
    assert client.get("/api/v2/profiles").json() == {
        "profiles": [{"id": original["id"], "name": original["name"]}]
    }
    assert client.get(route).json()["style"] == "保留的表达风格"
    edited = client.post(route, json={
        "name": "合成画像新版",
        "direction": "新方向",
        "audience": "新读者",
        "goal": "新目标",
        "version": original["version"],
    })
    assert edited.status_code == 200, edited.text
    value = edited.json()
    assert (value["name"], value["direction"], value["audience"], value["goal"]) == (
        "合成画像新版", "新方向", "新读者", "新目标")
    assert value["version"] == original["version"] + 1
    assert value["style"] == "保留的表达风格"
    assert value["preferences"] == "保留的红线"
    assert value["wechat_footer"] == "保留的公众号署名"
    assert app.post(article["id"])["profile_id"] == original["id"]
    assert app.post(article["id"])["version"] == article["version"]
    assert [row["version"] for row in profiles.history(app, original["id"])["revisions"]] == [2, 1]


def test_stale_edit_is_rejected_and_new_profile_does_not_link_article(ctx):
    app, original, article, client = ctx
    route = f"/api/v2/profiles/{original['id']}"
    fields = {"direction": "原方向", "audience": "原读者", "goal": "原目标"}
    first = client.post(route, json={"name": "首次修改", "version": 1, **fields})
    assert first.status_code == 200
    stale = client.post(route, json={"name": "旧页面覆盖", "version": 1, **fields})
    assert stale.status_code == 409
    assert profiles.get(app, original["id"])["name"] == "首次修改"
    assert len(profiles.history(app, original["id"])["revisions"]) == 2
    assert client.post(route, json={"name": "不能清空其余字段", "version": 2}).status_code == 422
    assert profiles.get(app, original["id"])["direction"] == "原方向"
    created = client.post("/api/v2/profiles", json={
        "name": "第二画像", "direction": "新定位", "audience": "", "goal": ""
    })
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1
    assert app.post(article["id"])["profile_id"] == original["id"]


def test_profile_editor_in_browser(tmp_path):
    service = PreviewService(tmp_path / "profile-browser")
    service.start()
    try:
        fixture = tmp_path / "profile-browser.json"
        fixture.write_text(json.dumps({
            "url": service.origin,
            "screenshot": str(ROOT / "test-results/m4.3.1-profile-panel.png"),
        }), encoding="utf-8")
        result = subprocess.run(
            ["node", str(ROOT / "tests/verify_preview_profiles.cjs"), str(fixture)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        created = next(row for row in profiles.listing(service.app) if row["name"] == "合成 P1 画像")
        assert created["audience"] == "另一窗口读者"
        assert service.app.db.one("SELECT COUNT(*) AS n FROM posts WHERE profile_id=?", (created["id"],))["n"] == 1
    finally:
        service.stop()
