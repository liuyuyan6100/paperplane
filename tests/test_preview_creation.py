"""M4.1 creation contracts, atomic adoption, restart and real browser workflow."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from studio.api.preview import create_app
from studio.db import uid
from studio.infrastructure.legacy_creation import LegacyCreation
from studio.preview_runtime import PreviewService
from studio.service import Problem, Studio

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPLANE_USE_SKILLS", "0")
    app = Studio(tmp_path / "creation")
    app.config.update(
        {"AI_MODE": "api", "AI_BASE_URL": "http://127.0.0.1:1/v1", "AI_MODEL": "synthetic"}
    )
    app.generate_draft = lambda *a, **k: "## 合成提纲\n\n依据选定素材展开。"
    post = app.save_post(
        {"title": "创作测试", "body": "原稿😀", "author": "作者", "source": "https://example.com/"}
    )
    port = LegacyCreation(app)
    with TestClient(
        create_app(app, "http://127.0.0.1:1", "synthetic", 43111), base_url="http://127.0.0.1:43111"
    ) as client:
        client.headers["X-Studio-Token"] = "synthetic"
        yield app, post, port, client
    app.close()


def inputs(post, **extra):
    return dict(
        request_id=uid(),
        version=post["version"],
        reference_ids=[],
        instruction="使用选定素材，先提纲后正文。",
        parent_id=None,
        reuse_confirmed=False,
        **extra,
    )


def review(ctx):
    app, post, port, _ = ctx
    task = port.create(post["id"], inputs(post))
    app.workflows.tick()
    task = port.get(post["id"], task["id"])
    app.workflows.confirm(
        task["id"],
        {
            "outline_version": task["outline_version"],
            "outline": task["outline"],
            "selected_title": post["title"],
        },
    )
    app.generate_draft = lambda *a, **k: "生成的完整正文😀\n\n基于已确认的提纲。"
    app.workflows.tick()
    return port.get(post["id"], task["id"])


def adoption(task, version, **extra):
    return (
        dict(
            version=version,
            result_signature=task["result_signature"],
            resolution="unchanged",
            content=None,
        )
        | extra
    )


def test_full_creation_material_outline_body_adoption_and_idempotence(ctx):
    app, post, port, client = ctx
    material = dict(
        request_id=uid(),
        title="选择的素材",
        body="明确来源的材料",
        source="https://example.com/source",
    )
    saved = client.post("/api/v2/creation/materials", json=material).json()
    assert client.post("/api/v2/creation/materials", json=material).json()["id"] == saved["id"]
    assert (
        client.post("/api/v2/creation/materials", json=material | {"body": "编号重用"}).status_code
        == 409
    )
    values = inputs(post) | {"reference_ids": [saved["id"]]}
    route = f"/api/v2/articles/{post['id']}/creation"
    task = client.post(route, json=values).json()
    assert client.post(route, json=values).json()["id"] == task["id"]
    assert client.post(route, json=values | {"instruction": "内容不同"}).status_code == 409
    app.workflows.tick()
    url = route + "/" + task["id"]
    task = client.get(url).json()
    assert task["status"] == "outline_review" and not app.workflows.tick()
    assert app.post(post["id"])["body"] == post["body"]
    edited = dict(
        outline_version=task["outline_version"],
        outline="## 手工调整的提纲\n\n明确材料边界。",
        selected_title="审阅后标题",
    )
    task = client.post(url + "/outline", json=edited).json()
    assert task["outline"] == edited["outline"] and not app.workflows.tick()
    confirm = edited | {"outline_version": task["outline_version"]}
    assert client.post(url + "/confirm", json=confirm).status_code == 200
    assert client.post(url + "/confirm", json=confirm).status_code == 200
    app.generate_draft = lambda *a, **k: "## 正文生成结果\n\n待作者审阅。"
    app.workflows.tick()
    task = client.get(url).json()
    assert task["status"] == "review" and app.post(post["id"])["body"] == post["body"]
    task = client.post(
        url + "/body",
        json={
            "result_signature": task["result_signature"],
            "content": {"title": "修改后的正文标题", "body": "作者审阅后的正文"},
        },
    ).json()
    assert app.post(post["id"])["body"] == post["body"]
    result = client.post(url + "/adopt", json=adoption(task, post["version"])).json()
    assert result["post"]["body"] == "作者审阅后的正文" and result["post"]["version"] == 2
    assert result["post"]["author"] == post["author"] and result["post"]["source"] == post["source"]
    assert client.post(url + "/adopt", json=adoption(task, 1)).json()["post"]["version"] == 2
    assert app.db.one("SELECT COUNT(*) n FROM revisions WHERE post_id=?", (post["id"],))["n"] == 2
    assert app.post(saved["id"])["body"] == saved["body"]
    assert port.get(post["id"], task["id"])["references"][0]["version"] == 1
    assert not app.jobs() and not app.db.rows("SELECT * FROM article_draft_intents")


@pytest.mark.parametrize("resolution", ["candidate", "merged"])
def test_conflict_requires_explicit_choice_preserves_original_snapshot(ctx, resolution):
    app, post, port, _ = ctx
    task = review(ctx)
    changed = app.save_post({"version": 1, "body": "生成期间人工新稿"}, post["id"])
    snapshot = app.db.one("SELECT snapshot FROM workflows WHERE id=?", (task["id"],))["snapshot"]
    with pytest.raises(Problem):
        port.adopt(post["id"], task["id"], adoption(task, changed["version"]))
    values = adoption(
        task,
        changed["version"],
        resolution=resolution,
        content={"title": "合并标题", "body": "合并两份后的正文"},
    )
    result = port.adopt(post["id"], task["id"], values)
    assert result["post"]["body"] == (
        "合并两份后的正文" if resolution == "merged" else task["result"]["body"]
    )
    assert result["post"]["version"] == 3
    assert (
        json.loads(
            app.db.one(
                "SELECT snapshot FROM revisions WHERE post_id=? AND version=2", (post["id"],)
            )["snapshot"]
        )["body"]
        == "生成期间人工新稿"
    )
    assert (
        app.db.one("SELECT snapshot FROM workflows WHERE id=?", (task["id"],))["snapshot"]
        == snapshot
    )
    assert result["workflow"]["result"]["body"] == result["post"]["body"]


def test_atomic_adoption_rolls_back_article_revision_and_task_together(ctx):
    app, post, port, _ = ctx
    task = review(ctx)
    with patch(
        "studio.infrastructure.legacy_creation.record",
        side_effect=OSError("synthetic disk failure"),
    ):
        with pytest.raises(OSError):
            port.adopt(post["id"], task["id"], adoption(task, 1))
    assert app.post(post["id"])["body"] == post["body"]
    assert app.post(post["id"])["version"] == 1
    assert port.get(post["id"], task["id"])["status"] == "review"
    assert app.db.one("SELECT COUNT(*) n FROM revisions WHERE post_id=?", (post["id"],))["n"] == 1


def test_concurrent_adoption_no_extra_revision_and_stale_candidate_rejected(ctx):
    app, post, port, _ = ctx
    task = review(ctx)
    app.workflows.result_edit(
        task["id"],
        {
            "result_signature": task["result_signature"],
            "content": {"title": "新标题", "body": "更新的候选"},
        },
    )
    with pytest.raises(Problem):
        port.adopt(post["id"], task["id"], adoption(task, 1))
    task = port.get(post["id"], task["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: port.change(post["id"], task["id"], "adopt", adoption(task, 1)), range(2)
            )
        )
    assert [r["post"]["version"] for r in results] == [2, 2]
    assert app.db.one("SELECT COUNT(*) n FROM revisions WHERE post_id=?", (post["id"],))["n"] == 2


def test_changed_inputs_preserve_outline_require_reconfirmation_and_same_inputs_reuse(ctx):
    app, post, port, _ = ctx
    task = review(ctx)
    same = port.create(
        post["id"], inputs(post) | {"parent_id": task["id"], "reuse_confirmed": True}
    )
    assert same["status"] == "queued" and same["confirmed_outline_version"] == 1
    revised = port.create(
        post["id"],
        inputs(post)
        | {"parent_id": task["id"], "instruction": "新的明确要求", "reuse_confirmed": True},
    )
    assert revised["status"] == "outline_review" and revised["outline"] == task["outline"]
    assert revised["confirmed_outline_version"] == 0
    app.workflows.cancel(same["id"])
    app.save_post({"body": "新的原稿", "version": 1}, post["id"])
    with pytest.raises(Problem):
        port.change(
            post["id"],
            revised["id"],
            "confirm",
            {"outline_version": 1, "outline": revised["outline"], "selected_title": post["title"]},
        )
    # A fresh task on the current saved article can retain the old outline safely.
    current = app.post(post["id"])
    fresh = port.create(post["id"], inputs(current) | {"parent_id": revised["id"]})
    assert fresh["outline"] == revised["outline"] and fresh["status"] == "outline_review"
    assert fresh["post_version"] == 2


def test_adopted_parent_reuses_confirmed_outline_without_reconfirmation(ctx):
    app, post, port, _ = ctx
    task = review(ctx)
    result = port.adopt(post["id"], task["id"], adoption(task, 1))
    fresh = port.create(
        post["id"], inputs(result["post"]) | {"parent_id": task["id"], "reuse_confirmed": True}
    )
    assert fresh["status"] == "queued" and fresh["confirmed_outline_version"] == 1
    assert fresh["outline"] == task["outline"]


def test_selected_material_snapshots_exclude_unselected_and_track_versions(ctx):
    app, post, port, _ = ctx
    chosen = app.save_post({"title": "选中素材", "body": "正确的选中材料"})
    other = app.save_post({"title": "不选素材", "body": "PRIVATE_UNSELECTED_MARKER"})
    task = port.create(post["id"], inputs(post) | {"reference_ids": [chosen["id"]]})
    app.save_post({"version": 1, "body": "资料后改版本"}, chosen["id"])
    captured = []
    app.generate_draft = lambda p, instruction, **kw: captured.append(instruction) or "## 合成提纲"
    app.workflows.tick()
    assert "正确的选中材料" in captured[0]
    assert "PRIVATE_UNSELECTED_MARKER" not in captured[0] and other["id"] not in captured[0]
    assert port.get(post["id"], task["id"])["input_changes"] == [chosen["title"]]
    assert "snapshot" not in port.get(post["id"], task["id"])
    assert "run_token" not in port.get(post["id"], task["id"])


def test_shared_import_is_read_only_versioned_and_idempotent(ctx):
    app, _, port, client = ctx
    material_id = uid()
    item = {
        "id": material_id,
        "name": "资料.md",
        "version": 3,
        "source": "合成来源",
        "relative_path": "资料.md",
        "sha256": "a" * 64,
    }
    with patch(
        "studio.shared_library.material", return_value=(item, "共享原文😀".encode())
    ) as read:
        values = {"request_id": uid(), "shared_id": material_id, "shared_version": 3}
        first = client.post("/api/v2/creation/materials", json=values)
        assert first.status_code == 200
        assert (
            client.post("/api/v2/creation/materials", json=values).json()["id"]
            == first.json()["id"]
        )
        assert read.call_count == 1
        assert "v3" in first.json()["source"] and first.json()["body"] == "共享原文😀"
    with patch(
        "studio.shared_library.listing",
        return_value={"items": [item, item | {"name": "不能作为文字.pdf"}]},
    ):
        assert len(port.shared()["items"]) == 1
    assert not app.jobs()


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.test"},
        {"Origin": "https://evil.test"},
        {"X-Studio-Token": "bad"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_creation_boundary_rejects_untrusted_mutations(ctx, headers):
    app, post, _, client = ctx
    assert (
        client.post(
            f"/api/v2/articles/{post['id']}/creation", json=inputs(post), headers=headers
        ).status_code
        == 403
    )
    assert not app.db.rows("SELECT * FROM workflows")


def test_typed_inputs_ownership_invalid_versions_and_material_validation(ctx):
    app, post, port, client = ctx
    route = f"/api/v2/articles/{post['id']}/creation"
    assert client.post(route, json=inputs(post) | {"version": True}).status_code == 422
    assert client.post(route, json=inputs(post) | {"reference_ids": [uid()] * 7}).status_code == 422
    assert client.post(route, json=inputs(post) | {"version": 9}).status_code == 409
    assert (
        client.post(
            "/api/v2/creation/materials", json={"request_id": uid(), "title": "空素材"}
        ).status_code
        == 422
    )
    task = review(ctx)
    other = app.save_post({"title": "其他文章"})
    assert client.get(f"/api/v2/articles/{other['id']}/creation/{task['id']}").status_code == 404
    app.archive(post["id"], True)
    with pytest.raises(Problem):
        port.adopt(post["id"], task["id"], adoption(task, 2, resolution="candidate"))


def test_restart_keeps_materials_pending_body_and_no_automatic_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPLANE_USE_SKILLS", "0")
    service = PreviewService(tmp_path / "restart")
    service.start()
    app = service.app
    post = app.save_post({"title": "重启原稿", "body": "不覆盖"})
    port = LegacyCreation(app)
    task = port.create(post["id"], inputs(post))
    # No usable model configured, worker may mark failed; explicit queued state simulates interrupted enqueue.
    service.stop()
    with app.db.connect() as db:
        db.execute("UPDATE workflows SET status='queued' WHERE id=?", (task["id"],))
    second = PreviewService(tmp_path / "restart")
    calls = []
    second.configure = lambda value: setattr(
        value, "generate_draft", lambda *a, **k: calls.append(1) or "unexpected"
    )
    second.start()
    try:
        assert LegacyCreation(second.app).get(post["id"], task["id"])["status"] == "interrupted"
        assert not calls and second.app.post(post["id"])["body"] == "不覆盖"
    finally:
        second.stop()


def test_creation_browser_materials_outline_review_conflict_and_adoption(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERPLANE_USE_SKILLS", "0")
    calls = []
    seed = {}

    def configure(app):
        app.config.update(
            {"AI_MODE": "api", "AI_BASE_URL": "http://127.0.0.1:1/v1", "AI_MODEL": "synthetic"}
        )
        seed.update(
            app.save_post(
                {"title": "M4创作浏览器验收", "body": "已经保存的原稿😀", "author": "手写作者"}
            )
        )

        def model(post, instruction, structured=False):
            calls.append(instruction)
            return (
                "## 合成章节一\n\n材料边界与待补证据。"
                if "本次只生成" in instruction
                else "合成完整正文😀\n\n确认提纲之后生成的内容。"
            )

        app.generate_draft = model

    service = PreviewService(tmp_path / "browser")
    service.configure = configure
    service.start()
    fixture = tmp_path / "browser.json"
    fixture.write_text(
        json.dumps(
            {
                "url": service.origin,
                "post_id": seed["id"],
                "output": str(ROOT / "test-results/m4.1-browser"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["node", str(ROOT / "tests/verify_preview_creation.cjs"), str(fixture)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(calls) == 2
        assert service.app.post(seed["id"])["body"] == "手动合并保留当前稿与候选😀"
        assert not service.app.jobs()
    finally:
        service.stop()
