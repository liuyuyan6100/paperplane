"""M3: persisted projections, explicit recovery and stale-execution fault injection."""

import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from studio.api.preview import create_app
from studio.db import dumps, now, uid
from studio.domain.tasks import ExecutionLease
from studio.infrastructure.legacy_tasks import LegacyTasks, digest, prepare_preview_restart
from studio.service import Problem, Studio

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    value = Studio(tmp_path / "isolated")
    value.config.update(
        {
            "AI_MODE": "api",
            "AI_BASE_URL": "http://127.0.0.1:1/v1",
            "AI_MODEL": "synthetic",
            "DEVTO_API_KEY": "synthetic-secret",
        }
    )
    yield value
    value.close()


def article(app, title="M3合成文章"):
    saved = app.save_post(
        {"title": title, "body": "作者原稿，不可被任务刷新覆盖", "stage": "draft"}
    )
    return app.post(saved["id"])


def workflow(app, state="failed"):
    post = article(app)
    task = app.workflows.create(
        {
            "post_id": post["id"],
            "version": post["version"],
            "reference_ids": [],
            "instruction": "先提纲，确认后生成正文",
        }
    )
    with app.db.connect(immediate=True) as db:
        db.execute(
            "UPDATE workflows SET status=?,error='合成401认证失败' WHERE id=?", (state, task["id"])
        )
    return task["id"], post


def delivery(app, state="failed", external_id="", channel="devto"):
    post = article(app, "M3合成送稿")
    task_id = uid()
    snapshot = {
        **post,
        "delivery_config": {"mode": "api" if channel == "wechat" else "manual"},
        "private_sentinel": "SYNTHETIC_SNAPSHOT_NOT_PUBLIC",
    }
    with app.db.connect(immediate=True) as db:
        db.execute(
            "INSERT INTO jobs(id,run_id,post_id,channel_id,version,snapshot,status,due_at,created_at,updated_at,external_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                uid(),
                post["id"],
                channel,
                post["version"],
                dumps(snapshot),
                state,
                now(),
                now(),
                now(),
                external_id,
            ),
        )
    return task_id, post


def command(repo, kind, task_id, action, **extra):
    return {
        "request_id": uid(),
        "revision": repo.detail(kind, task_id)["revision"],
        "action": action,
        "verified": False,
        **extra,
    }


def test_task_projection_filters_pagination_and_secret_allowlist(app):
    repo = LegacyTasks(app)
    task_id, post = workflow(app)
    delivery_id, _ = delivery(app, "uncertain", "original-draft-id", "wechat")
    result = repo.listing(query="M3合成")
    assert result["total"] == 2 and result["counts"]["attention"] == 2
    assert len(repo.listing(limit=1)["tasks"]) == 1
    assert (
        repo.listing(offset=1, limit=1)["tasks"][0]["id"] != repo.listing(limit=1)["tasks"][0]["id"]
    )
    assert repo.listing(kind="workflow", post_id=post["id"])["total"] == 1
    detail = repo.detail("workflow", task_id)
    assert detail["status_label"] == "执行失败" and detail["error_kind"] == "configuration"
    assert detail["events"] and "retry" in detail["actions"]
    assert detail["legacy_url"] == "/#workflows/" + task_id
    unknown = repo.detail("delivery", delivery_id)
    assert unknown["error_kind"] == "unknown_delivery" and "retry" not in unknown["actions"]
    assert unknown["receipt"]["external_id"] == "original-draft-id"
    encoded = dumps([result, detail, unknown])
    for forbidden in (
        "SYNTHETIC_SNAPSHOT_NOT_PUBLIC",
        "synthetic-secret",
        "run_token",
        "snapshot",
        "route",
    ):
        assert forbidden not in encoded
    assert repo.listing(query="%")["total"] == 0


def test_task_visibility_batch_restore_and_protected_delivery(app):
    repo = LegacyTasks(app)
    first, post = workflow(app)
    second, _ = workflow(app)
    unknown, _ = delivery(app, "uncertain", "existing-draft", "wechat")
    running, _ = workflow(app, "running")
    before = app.post(post["id"])
    items = [
        {"kind": "workflow", "id": task_id,
         "revision": repo.detail("workflow", task_id)["visibility_revision"]}
        for task_id in (first, second, running)
    ]
    items.append({"kind": "delivery", "id": unknown,
                  "revision": repo.detail("delivery", unknown)["visibility_revision"]})
    result = repo.change_visibility("archive", items)
    assert len(result["succeeded"]) == 2 and len(result["failed"]) == 2
    assert repo.listing(view="archived")["total"] == 2
    assert repo.listing(view="active")["total"] == 2
    assert app.post(post["id"]) == before
    stale = repo.change_visibility("trash", [items[0]])
    assert stale["failed"] and "变化" in stale["failed"][0]["message"]
    archived = repo.detail("workflow", first)
    assert repo.change_visibility("trash", [{"kind": "workflow", "id": first,
        "revision": archived["visibility_revision"]}])["succeeded"]
    assert repo.listing(view="trash")["total"] == 1
    repo = LegacyTasks(app)  # visibility survives another repository instance
    trashed = repo.detail("workflow", first)
    assert repo.change_visibility("restore", [{"kind": "workflow", "id": first,
        "revision": trashed["visibility_revision"]}])["succeeded"]
    assert repo.detail("workflow", first)["visibility"] == "active"
    assert repo.listing(view="trash")["total"] == 0
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE workflows SET status='queued',updated_at=? WHERE id=?", (now(), second))
    assert repo.detail("workflow", second)["visibility"] == "active"
    assert repo.listing(view="archived")["total"] == 0


def test_overdue_is_a_view_only_and_does_not_hide_uncertain_delivery(app):
    repo = LegacyTasks(app)
    old, _ = workflow(app)
    unknown, _ = delivery(app, "uncertain", "existing-draft", "wechat")
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE workflows SET updated_at='2020-01-01T00:00:00+00:00' WHERE id=?", (old,))
        db.execute("UPDATE jobs SET updated_at='2020-01-01T00:00:00+00:00' WHERE id=?", (unknown,))
    page = repo.listing(group="overdue")
    assert page["total"] == 1 and page["tasks"][0]["id"] == old
    assert page["tasks"][0]["overdue"]
    assert repo.detail("workflow", old)["status"] == "failed"
    assert repo.detail("delivery", unknown)["status"] == "uncertain"
    assert repo.listing(group="attention")["total"] == 2


def test_every_existing_source_projects_without_rebuilding_its_table(app):
    from studio import profiles

    repo = LegacyTasks(app)
    post = article(app)
    profile = profiles.save(app, {"name": "合成画像"})
    ids = {}

    def insert(table, **overrides):
        with app.db.connect(immediate=True) as db:
            columns = db.execute("PRAGMA table_info(" + table + ")").fetchall()
            values = {}
            for col in columns:
                key = col["name"]
                if col["dflt_value"] is not None or not (col["notnull"] or col["pk"]):
                    continue
                values[key] = 0 if col["type"] == "INTEGER" else ""
            values.update(id=uid(), status="failed")
            for key in list(values):
                if key.endswith("_at"):
                    values[key] = now()
                if key in ("post_id", "source_post_id"):
                    values[key] = post["id"]
                if key == "profile_id":
                    values[key] = profile["id"]
                if key in ("snapshot", "content", "result"):
                    values[key] = dumps(
                        {"post": post, "post_id": post["id"], "route_secret": "DO_NOT_EXPOSE"}
                    )
            values.update(overrides)
            valid = {r["name"] for r in columns}
            values = {k: v for k, v in values.items() if k in valid}
            db.execute(
                "INSERT INTO "
                + table
                + "("
                + ",".join(values)
                + ") VALUES("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
            return values.get("id", values.get("post_id"))

    insert("conversations", id="c" * 32, profile_id=profile["id"])
    ids["workflow"] = insert(
        "workflows", post_title=post["title"], reference_ids="[]", stage="generate"
    )
    ids["assistant"] = insert(
        "editor_assistant_turns", kind="post", target_id=post["id"], mode="edit", message="合成修改"
    )
    ids["delivery"] = delivery(app)[0]
    ids["topic"] = insert("topic_plans", status="completed")
    ids["repurpose"] = insert("repurpose_variants", batch_id=uid(), platform="devto")
    ids["illustration"] = insert("illustration_plans")
    ids["image"] = insert("illustration_candidates", plan_id=ids["illustration"], page_index=0)
    ids["visual"] = insert(
        "platform_visual_pages", variant_id=ids["repurpose"], version=1, page_index=0, page="{}"
    )
    ids["review"] = insert("editorial_reviews")
    ids["performance"] = insert("performance_reviews", job_id=ids["delivery"], sample_id=uid())
    ids["preparation"] = insert("draft_preparations", signature="a" * 64)
    ids["conversation"] = insert("conversation_turns", conversation_id="c" * 32, message="合成对话")
    ids["learning"] = insert(
        "profile_learning_events", source_id=uid(), source_version=1, kind="feedback", platform=""
    )
    ids["draft-intent"] = insert("article_draft_intents", post_id=post["id"], status="queued")
    result = repo.listing(limit=100)
    assert {item["kind"] for item in result["tasks"]} == set(ids)
    for kind, task_id in ids.items():
        detail = repo.detail(kind, task_id)
        assert detail["id"] == task_id
        assert "DO_NOT_EXPOSE" not in dumps(detail)
    assert repo.detail("topic", ids["topic"])["group"] == "review"
    assert repo.detail("draft-intent", ids["draft-intent"])["group"] == "done"


def test_retry_is_same_task_and_duplicate_command_never_replays_even_after_failure(app):
    repo = LegacyTasks(app)
    task_id, post = workflow(app)
    values = command(repo, "workflow", task_id, "retry")
    before = app.post(post["id"])
    count = app.db.one("SELECT COUNT(*) n FROM workflows")["n"]
    first = repo.command("workflow", task_id, values)
    assert repo.detail("workflow", task_id)["status"] == "queued"
    with app.db.connect() as db:
        db.execute(
            "UPDATE workflows SET status='failed',error='合成再次失败' WHERE id=?", (task_id,)
        )
    assert repo.command("workflow", task_id, values) == first
    assert repo.detail("workflow", task_id)["status"] == "failed"
    assert app.db.one("SELECT COUNT(*) n FROM workflows")["n"] == count
    assert app.post(post["id"]) == before
    with pytest.raises(Problem, match="同一操作编号"):
        repo.command("workflow", task_id, {**values, "action": "cancel"})


def test_concurrent_duplicate_commands_enqueue_once(app):
    repo = LegacyTasks(app)
    task_id, _ = workflow(app)
    values = command(repo, "workflow", task_id, "retry")
    original = app.workflows.retry
    with (
        patch.object(app.workflows, "retry", wraps=original) as retry,
        ThreadPoolExecutor(2) as pool,
    ):
        results = list(pool.map(lambda _: repo.command("workflow", task_id, values), range(2)))
    assert results[0] == results[1] and retry.call_count == 1


def test_stale_commands_cannot_cancel_or_retry_newer_state(app):
    repo = LegacyTasks(app)
    task_id, _ = workflow(app)
    values = command(repo, "workflow", task_id, "retry")
    with app.db.connect() as db:
        db.execute("UPDATE workflows SET error='人工已处理' WHERE id=?", (task_id,))
    with pytest.raises(Problem, match="任务已变化"):
        repo.command("workflow", task_id, values)
    assert app.db.one("SELECT COUNT(*) n FROM task_commands")["n"] == 0


def test_unknown_delivery_requires_explicit_verified_step_before_manual_retry(app):
    repo = LegacyTasks(app)
    task_id, _ = delivery(app, "uncertain")
    with pytest.raises(Problem, match="不能直接重试"):
        repo.command("delivery", task_id, command(repo, "delivery", task_id, "retry"))
    with pytest.raises(Problem, match="明确勾选"):
        repo.command("delivery", task_id, command(repo, "delivery", task_id, "not_received"))
    repo.command(
        "delivery", task_id, command(repo, "delivery", task_id, "not_received", verified=True)
    )
    assert repo.detail("delivery", task_id)["status"] == "failed"
    assert app.db.one("SELECT attempts FROM jobs WHERE id=?", (task_id,))["attempts"] == 0
    repo.command("delivery", task_id, command(repo, "delivery", task_id, "retry"))
    assert repo.detail("delivery", task_id)["status"] == "queued"
    assert app.db.one("SELECT COUNT(*) n FROM jobs")["n"] == 1


def test_crash_after_command_dispatch_is_not_automatically_replayed(app):
    repo = LegacyTasks(app)
    task_id, _ = workflow(app)
    values = command(repo, "workflow", task_id, "retry")

    def interrupted(*args):
        app.workflows.retry(task_id)
        raise RuntimeError("synthetic transport loss after commit")

    with patch.object(repo, "_dispatch", side_effect=interrupted) as dispatched:
        with pytest.raises(Problem, match="尚未确认"):
            repo.command("workflow", task_id, values)
        with pytest.raises(Problem, match="不会自动重复执行"):
            repo.command("workflow", task_id, values)
        assert dispatched.call_count == 1
    assert repo.detail("workflow", task_id)["status"] == "queued"
    assert app.db.one("SELECT status FROM task_commands")["status"] == "unknown"


def test_preview_restart_holds_queued_generation_and_unfinished_commands(app):
    repo = LegacyTasks(app)
    task_id, _ = workflow(app, "queued")
    values = command(repo, "workflow", task_id, "cancel")
    with app.db.connect() as db:
        db.execute(
            "INSERT INTO task_commands(request_id,kind,task_id,fingerprint,action,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
            (
                values["request_id"],
                "workflow",
                task_id,
                digest(["workflow", task_id, "cancel", values["revision"], False]),
                "cancel",
                now(),
                now(),
            ),
        )
    app.generate_draft = lambda *a, **k: pytest.fail("restart must never invoke model")
    prepare_preview_restart(app)
    assert repo.detail("workflow", task_id)["status"] == "interrupted"
    assert app.db.one("SELECT status FROM task_commands")["status"] == "unknown"
    with pytest.raises(Problem, match="不会自动重复执行"):
        repo.command("workflow", task_id, values)


def test_worker_cancel_keeps_original_article_and_ignores_late_model(app):
    repo = LegacyTasks(app)
    task_id, post = workflow(app, "queued")
    started = threading.Event()
    release = threading.Event()

    def model(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return "## 合成提纲\n待确认内容"

    app.generate_draft = model
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(app.workflows.tick)
        try:
            assert started.wait(5)
            repo.command("workflow", task_id, command(repo, "workflow", task_id, "cancel"))
        finally:
            release.set()
        assert future.result(timeout=5)
    assert repo.detail("workflow", task_id)["status"] == "canceled"
    assert app.post(post["id"]) == post


def test_old_delivery_receipt_cannot_overwrite_new_attempt_and_is_retained(app):
    task_id, _ = delivery(app, "queued")

    def send(*args, **kwargs):
        with app.db.connect() as db:
            db.execute(
                "UPDATE jobs SET status='draft',attempts=2,run_token='new-lease',external_id='new-receipt',note='新结果' WHERE id=?",
                (task_id,),
            )
        return {"status": "draft", "external_id": "old-receipt", "note": "旧结果"}

    with patch("studio.adapters.devto_send", side_effect=send):
        assert app.tick()
    row = app.db.one("SELECT * FROM jobs WHERE id=?", (task_id,))
    assert row["external_id"] == "new-receipt" and row["note"] == "新结果"
    assert "old-receipt" in dumps(app.job_detail(task_id)["events"])


def test_late_receipt_quarantines_a_not_yet_started_retry(app):
    task_id, _ = delivery(app, "queued")

    def send(*args, **kwargs):
        with app.db.connect() as db:
            db.execute("UPDATE jobs SET status='uncertain',run_token='' WHERE id=?", (task_id,))
        app.job_action(task_id, "not_received", {"verified": True})
        app.job_action(task_id, "retry", {})
        return {"status": "draft", "external_id": "late-original", "note": "原请求收到"}

    with patch("studio.adapters.devto_send", side_effect=send):
        assert app.tick()
    row = app.db.one("SELECT * FROM jobs WHERE id=?", (task_id,))
    assert (
        row["status"] == "uncertain"
        and row["external_id"] == "late-original"
        and row["attempts"] == 1
    )
    assert not app.tick()


def test_shutdown_late_receipt_preserves_upload_evidence_but_not_success_state(app):
    task_id, _ = delivery(app, "queued")

    def send(*args, **kwargs):
        with app.db.connect() as db:
            row = db.execute("SELECT snapshot FROM jobs WHERE id=?", (task_id,)).fetchone()
            snapshot = json.loads(row["snapshot"])
            snapshot["desktop_submission"] = {"account_id": "synthetic", "body": "已上传内容"}
            db.execute("UPDATE jobs SET snapshot=? WHERE id=?", (dumps(snapshot), task_id))
        app.close()
        return {"status": "draft", "external_id": "late-after-stop", "note": "晚到回执"}

    with patch("studio.adapters.devto_send", side_effect=send):
        assert app.tick()
    row = app.db.one("SELECT * FROM jobs WHERE id=?", (task_id,))
    assert row["status"] == "uncertain" and row["external_id"] == "late-after-stop"
    assert json.loads(row["snapshot"])["desktop_submission"]["body"] == "已上传内容"


def test_two_delivery_ticks_claim_one_execution(app):
    delivery(app, "queued")
    started = threading.Event()
    release = threading.Event()

    def send(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return {"status": "draft", "note": "合成回执", "external_id": "one"}

    with (
        patch("studio.adapters.devto_send", side_effect=send) as sender,
        ThreadPoolExecutor(2) as pool,
    ):
        first = pool.submit(app.tick)
        try:
            assert started.wait(5)
            assert pool.submit(app.tick).result(timeout=5) is False
        finally:
            release.set()
        assert first.result(timeout=5) and sender.call_count == 1


def test_backup_restore_preserves_inputs_media_configuration_and_manual_recovery(app, tmp_path):
    from PIL import Image

    task_id, post = workflow(app, "queued")
    media = app.db.data_dir / "media"
    media.mkdir(exist_ok=True)
    image = media / ("a" * 32 + ".png")
    Image.new("RGB", (2, 2), "white").save(image)
    original_image = image.read_bytes()
    private = (app.db.data_dir / "private.json").read_bytes()
    backup = app.db.backup()
    restored = tmp_path / "restored"
    restored.mkdir()
    shutil.copy2(backup, restored / "studio.sqlite3")
    (restored / "private.json").write_bytes(private)
    shutil.copytree(media, restored / "media")
    recovered = Studio(restored)
    try:
        prepare_preview_restart(recovered)
        repo = LegacyTasks(recovered)
        assert recovered.post(post["id"]) == post
        assert (restored / "media" / image.name).read_bytes() == original_image
        assert recovered.config.get("DEVTO_API_KEY") == "synthetic-secret"
        assert (restored / "private.json").read_bytes() == private
        assert repo.detail("workflow", task_id)["status"] == "interrupted"
        assert recovered.db.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    finally:
        recovered.close()


def test_task_http_contract_security_and_unknown_state(app):
    task_id, _ = workflow(app)
    with TestClient(
        create_app(app, "http://127.0.0.1:1", "synthetic-token", 9999),
        base_url="http://127.0.0.1:9999",
    ) as client:
        assert client.get("/api/v2/tasks").status_code == 200
        detail = client.get("/api/v2/tasks/workflow/" + task_id).json()
        assert detail["stage_label"] and "snapshot" not in detail
        visibility = {"action": "archive", "items": [{"kind": "workflow", "id": task_id,
            "revision": detail["visibility_revision"]}]}
        assert client.post("/api/v2/tasks/visibility", json=visibility).status_code == 403
        assert client.post("/api/v2/tasks/visibility", json=visibility,
            headers={"X-Studio-Token": "synthetic-token"}).json()["succeeded"]
        assert client.get("/api/v2/tasks?view=archived").json()["total"] == 1
        restore = {"action": "restore", "items": [{"kind": "workflow", "id": task_id,
            "revision": client.get("/api/v2/tasks/workflow/" + task_id).json()["visibility_revision"]}]}
        assert client.post("/api/v2/tasks/visibility", json=restore,
            headers={"X-Studio-Token": "synthetic-token"}).json()["succeeded"]
        payload = {"request_id": uid(), "revision": detail["revision"], "action": "retry"}
        route = "/api/v2/tasks/workflow/" + task_id + "/commands"
        assert client.post(route, json=payload).status_code == 403
        assert (
            client.post(
                route,
                json=payload,
                headers={"X-Studio-Token": "synthetic-token", "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        headers = {"X-Studio-Token": "synthetic-token"}
        assert (
            client.post(route, json={**payload, "verified": "true"}, headers=headers).status_code
            == 422
        )
        assert (
            client.post(route, json={**payload, "snapshot": {}}, headers=headers).status_code == 422
        )
        assert client.get("/api/v2/tasks?limit=1001").status_code == 422
        assert client.get("/api/v2/tasks?kind=unknown").status_code == 422
        assert client.post(route, json=payload, headers=headers).status_code == 200
        assert client.post(route, json=payload, headers=headers).status_code == 200
        with app.db.connect() as db:
            db.execute("UPDATE workflows SET status='future_state' WHERE id=?", (task_id,))
        fresh = client.get("/api/v2/tasks/workflow/" + task_id).json()
        assert fresh["group"] == "attention" and fresh["actions"] == []
        assert client.get("/api/v2/tasks?group=attention").json()["total"] == 1


def test_execution_lease_distinguishes_new_attempts():
    first = ExecutionLease.next(0)
    second = ExecutionLease.next(1)
    assert first.attempt == 1 and second.attempt == 2 and first.token != second.token
    assert first.owns({"status": "running", "run_token": first.token})
    assert not first.owns({"status": "uncertain", "run_token": first.token})
    assert not first.owns({"status": "running", "run_token": second.token})


def test_delivery_lease_upgrade_keeps_old_rows_and_configuration(app):
    import sqlite3

    task_id, post = delivery(app)
    original = app.db.one("SELECT * FROM jobs WHERE id=?", (task_id,))
    original.pop("run_token")
    private = (app.db.data_dir / "private.json").read_bytes()
    app.close()
    with sqlite3.connect(app.db.path) as db:
        db.execute("ALTER TABLE jobs DROP COLUMN run_token")
    reopened = Studio(app.db.data_dir)
    try:
        raw = reopened.db.one("SELECT * FROM jobs WHERE id=?", (task_id,))
        assert raw.pop("run_token") == ""
        assert raw == original and reopened.post(post["id"]) == post
        assert (reopened.db.data_dir / "private.json").read_bytes() == private
        assert "run_token" not in dumps(reopened.jobs())
        assert "run_token" not in dumps(reopened.job_detail(task_id))
    finally:
        reopened.close()


def test_readonly_wechat_verification_does_not_commit_over_a_new_attempt(app):
    task_id, _ = delivery(app, "uncertain", "original", "wechat")

    def verify(*args, **kwargs):
        with app.db.connect() as db:
            db.execute(
                "UPDATE jobs SET attempts=2,run_token='other',external_id='new-receipt' WHERE id=?",
                (task_id,),
            )
        return {"status": "draft", "note": "旧草稿已回读"}

    with patch("studio.wechat.verify_draft", side_effect=verify):
        with pytest.raises(Problem, match="未覆盖最新结果"):
            app.recheck_wechat(task_id)
    assert (
        app.db.one("SELECT external_id FROM jobs WHERE id=?", (task_id,))["external_id"]
        == "new-receipt"
    )


def test_preview_runtime_restart_does_not_reinvoke_queued_generation(tmp_path):
    from studio.preview_runtime import PreviewService

    data = tmp_path / "runtime"
    first = PreviewService(data)
    first.start()
    try:
        first.app.workflows.stop()
        task_id, post = workflow(first.app, "queued")
    finally:
        first.stop()
    second = PreviewService(data)
    calls = []

    def configure(value):
        value.generate_draft = lambda *args, **kwargs: calls.append("unexpected") or "unexpected"

    second.configure = configure
    second.start()
    try:
        assert second.app.post(post["id"]) == post
        assert LegacyTasks(second.app).detail("workflow", task_id)["status"] == "interrupted"
        assert not calls and not second.app.workflows.tick()
        assert second.app.db.one("SELECT COUNT(*) n FROM workflows")["n"] == 1
    finally:
        second.stop()


def test_tasks_actual_browser_keeps_editor_and_recovers_original_workflow(tmp_path):
    import time

    from studio.preview_runtime import PreviewService

    release = tmp_path / "release-model"
    model_calls = []
    seed = {}

    def configure(value):
        value.config.update(
            {"AI_MODE": "api", "AI_BASE_URL": "http://127.0.0.1:1/v1", "AI_MODEL": "synthetic"}
        )
        task_id, post = workflow(value)
        seed.update(workflow_id=task_id, workflow_post=post["id"])
        seed["delivery_id"] = delivery(value, "uncertain")[0]

        def model(post, instruction, structured=False):
            model_calls.append(structured)
            if structured:
                deadline = time.monotonic() + 50
                while not release.exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("synthetic release deadline")
                    time.sleep(0.03)
                return dumps({"reply": "M3合成讨论完成", "replacement": None})
            return "## M3合成提纲\n\n先核对结构，再确认正文生成。"

        value.generate_draft = model

    service = PreviewService(tmp_path / "browser-data")
    service.configure = configure
    service.start()
    fixture = tmp_path / "browser.json"
    fixture.write_text(
        dumps(
            {
                "url": service.origin,
                "release": str(release),
                "output": str(ROOT / "test-results/m3-browser"),
                **seed,
            }
        ),
        encoding="utf-8",
    )
    try:
        with patch("studio.models.available", return_value={"models": ["synthetic"]}):
            result = subprocess.run(
                ["node", str(ROOT / "tests/verify_preview_tasks.cjs"), str(fixture)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            service.app.db.one("SELECT attempts FROM jobs WHERE id=?", (seed["delivery_id"],))[
                "attempts"
            ]
            == 0
        )
        assert len(model_calls) == 2
    finally:
        release.touch()
        service.stop()
