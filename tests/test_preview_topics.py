"""P1.2 reuses durable topic planning without exposing input snapshots."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from studio import profiles
from studio.api.preview import create_app
from studio.db import dumps
from studio.preview_runtime import PreviewService
from studio.service import Studio
from studio.topics import FIELDS

ROOT = Path(__file__).resolve().parents[1]


def model_response():
    return dumps({"candidates": [
        {key: f"合成候选{i + 1}" if key == "title" else f"{key}合成说明{i + 1}"
         for key in FIELDS}
        for i in range(3)
    ]})


@pytest.fixture
def ctx(tmp_path):
    app = Studio(tmp_path / "topics")
    profile = profiles.save(app, {"name": "合成账号", "audience": "入门读者"})
    with TestClient(create_app(app, "http://127.0.0.1:1", "synthetic", 43111),
                    base_url="http://127.0.0.1:43111") as client:
        client.headers["X-Studio-Token"] = "synthetic"
        yield app, profile, client
    app.close()


def test_topic_routes_reuse_snapshot_task_and_atomic_selection(ctx):
    app, profile, client = ctx
    values = {"request_id": "a" * 32, "profile_id": profile["id"], "idea": "用合成材料比较三个切入角度"}
    created = client.post("/api/v2/topics", json=values)
    assert created.status_code == 200, created.text
    plan = created.json()
    assert plan["status"] == "queued" and plan["candidates"] == []
    assert "context" not in plan and "snapshot" not in plan and "run_token" not in plan
    assert client.post("/api/v2/topics", json=values).json()["id"] == plan["id"]
    assert len(client.get("/api/v2/topics").json()["plans"]) == 1
    with patch.object(app, "generate_draft", return_value=model_response()):
        assert app.topics.tick()
    ready = client.get("/api/v2/topics/" + plan["id"])
    assert ready.status_code == 200
    assert ready.json()["status"] == "completed" and len(ready.json()["candidates"]) == 3
    route = "/api/v2/topics/" + plan["id"] + "/select"
    first = client.post(route, json={"index": 1})
    assert first.status_code == 200, first.text
    article = first.json()["post"]
    assert article["stage"] == "idea" and article["body"] == "" and article["profile_id"] == profile["id"]
    assert client.post(route, json={"index": 1}).json()["post"]["id"] == article["id"]
    assert client.post(route, json={"index": 2}).status_code == 409
    assert app.db.one("SELECT COUNT(*) AS n FROM posts WHERE id=?", (article["id"],))["n"] == 1


def test_topic_route_validation_and_original_retry(ctx):
    app, profile, client = ctx
    assert client.post("/api/v2/topics", json={"request_id": "b" * 32, "profile_id": profile["id"], "idea": " "}).status_code == 400
    assert client.post("/api/v2/topics", json={"request_id": "b" * 32, "profile_id": profile["id"], "idea": "主题", "reference_ids": []}).status_code == 422
    plan = client.post("/api/v2/topics", json={"request_id": "b" * 32, "profile_id": profile["id"], "idea": "有效想法"}).json()
    assert client.post("/api/v2/topics/" + plan["id"] + "/select", json={"index": 0}).status_code == 409
    with patch.object(app, "generate_draft", return_value="bad output"):
        app.topics.tick()
    assert client.get("/api/v2/topics/" + plan["id"]).json()["status"] == "failed"
    retried = client.post("/api/v2/topics/" + plan["id"] + "/retry", json={})
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    assert retried.json()["idea"] == "有效想法"
    assert len(app.topics.list()) == 1


def test_topic_planning_browser(tmp_path):
    service = PreviewService(tmp_path / "topic-browser")

    def configure(app):
        profiles.save(app, {"name": "合成账号", "audience": "入门读者"})
        app.generate_draft = lambda *_args, **_kwargs: model_response()

    service.configure = configure
    service.start()
    try:
        fixture = tmp_path / "topics.json"
        fixture.write_text(json.dumps({
            "url": service.origin,
            "screenshot": str(ROOT / "test-results/m4.3.2-topic-panel.png"),
        }), encoding="utf-8")
        result = subprocess.run(
            ["node", str(ROOT / "tests/verify_preview_topics.cjs"), str(fixture)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        plans = service.app.topics.list()
        assert len(plans) == 1 and plans[0]["selected_post_id"]
        assert service.app.post(plans[0]["selected_post_id"])["stage"] == "idea"
    finally:
        service.stop()
