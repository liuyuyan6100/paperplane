"""Verify frozen code/assets and a synthetic save/restart in the packaged service."""

import argparse
import base64
import hashlib
import io
import json
import marshal
import os
import sqlite3
import subprocess
import tempfile
import time
import types
from contextlib import nullcontext
from pathlib import Path

import httpx
from PIL import Image
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]


def normalize(code):
    if isinstance(code, types.CodeType):
        return (
            code.co_code,
            tuple(normalize(c) for c in code.co_consts),
            code.co_names,
            code.co_varnames,
            code.co_freevars,
            code.co_cellvars,
            code.co_flags,
            code.co_argcount,
            code.co_kwonlyargcount,
            code.co_posonlyargcount,
        )
    return code


def verify(package, output):
    exe = package / "PaperPlaneStudioPreview.exe"
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive(next(k for k in archive.toc if k.endswith(".pyz")))
    modules = [
        "studio.api.illustrations",
        "studio.application.illustrations",
        "studio.domain.illustrations",
        "studio.infrastructure.legacy_illustrations",
        "studio.illustrations",
        "studio.illustration_positions",
        "studio.api.preferences",
        "studio.preview_preferences",
        "studio.api.creation",
        "studio.application.creation",
        "studio.domain.creation",
        "studio.infrastructure.legacy_creation",
        "studio.article_metadata",
        "studio.api.tasks",
        "studio.application.tasks",
        "studio.domain.tasks",
        "studio.infrastructure.legacy_tasks",
        "studio.infrastructure.delivery_execution",
        "studio.api.preview",
        "studio.api.articles",
        "studio.api.profiles",
        "studio.application.profiles",
        "studio.domain.profiles",
        "studio.infrastructure.legacy_profiles",
        "studio.api.topics",
        "studio.application.topics",
        "studio.domain.topics",
        "studio.infrastructure.legacy_topics",
        "studio.api.assistant",
        "studio.application.assistant",
        "studio.domain.assistant",
        "studio.infrastructure.legacy_assistant",
        "studio.infrastructure.configuration_reuse",
        "studio.application.articles",
        "studio.domain.articles",
        "studio.infrastructure.legacy_articles",
        "studio.infrastructure.article_cleanup",
        "studio.preview_runtime",
        "studio.server",
        "studio.service",
        "studio.db",
        "studio.profiles",
        "studio.models",
        "studio.model_routing",
        "studio.image_provider",
        "studio.editor_assistant",
        "studio.workflows",
        "studio.markdown",
    ]
    for name in modules:
        source = ROOT / (name.replace(".", "/") + ".py")
        assert normalize(pyz.extract(name)) == normalize(
            compile(source.read_bytes(), str(source), "exec")
        ), name
    assert normalize(marshal.loads(archive.extract("preview"))) == normalize(
        compile((ROOT / "preview.py").read_bytes(), "preview.py", "exec")
    )
    resources = []
    for folder in ("frontend/dist", "web", "skills", "agents"):
        for source in (ROOT / folder).rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts:
                relative = source.relative_to(ROOT)
                assert source.read_bytes() == (package / "_internal" / relative).read_bytes(), str(
                    relative
                )
                resources.append(relative.as_posix())
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep synthetic evidence; cleanup failures must not mask the actual check.
    with nullcontext(tempfile.mkdtemp(prefix="m1-package-", dir=output.parent)) as scratch:
        data = Path(scratch) / "isolated-data"
        marker = data / "preview-runtime.json"
        process = None
        runtime_pid = None

        def start():
            nonlocal process, runtime_pid
            marker.unlink(missing_ok=True)
            process = subprocess.Popen(
                [str(exe), "--serve", "--data-dir", str(data)],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 40
            while True:
                assert process.poll() is None, f"packaged startup exit {process.returncode}"
                try:
                    info = json.loads(marker.read_text(encoding="utf-8"))
                    runtime_pid = info["pid"]
                    break
                except (OSError, ValueError, KeyError):
                    pass  # The runtime marker may still be in the middle of its first write.
                if time.monotonic() > deadline:
                    raise TimeoutError("Packaged startup did not report readiness")
                time.sleep(0.1)
            assert Path(info["data_dir"]).resolve() == data.resolve()
            return info["url"].removesuffix("/editor/")

        def stop():
            nonlocal process, runtime_pid
            if runtime_pid and process and runtime_pid != process.pid:
                try:
                    os.kill(runtime_pid, 15)  # PID reported by this fixture's frozen child.
                except ProcessLookupError:
                    pass
            if process and process.poll() is None:
                if runtime_pid == process.pid:
                    process.terminate()
                process.wait(timeout=15)
            process = None
            runtime_pid = None

        try:
            origin = start()
            with httpx.Client(base_url=origin, trust_env=False) as client:
                assert client.get("/preview-healthz").json()["worker_alive"]
                assert client.get("/editor/").status_code == 200
                session = client.get("/api/v2/session").json()
                assert session["version"] == "0.4.0-m4.3.3-ui" and session["milestone"] == "M4"
                tasks = client.get("/api/v2/tasks").json()
                assert tasks["total"] == 0 and len(tasks["kinds"]) == 14
                client.headers["X-Studio-Token"] = session["token"]
                response = client.post(
                    "/api/v2/articles", json={"title": "成品保存验收", "body": "成品原始内容😀"}
                )
                assert response.status_code == 200
                article = response.json()
                assert article["stage"] == "draft"
                profile_fields = {"name": "成品合成画像", "direction": "教程", "audience": "入门读者", "goal": "清晰表达"}
                profile_response = client.post("/api/v2/profiles", json=profile_fields)
                assert profile_response.status_code == 200, profile_response.text
                profile = profile_response.json()
                assert client.get("/api/v2/profiles").json()["profiles"][0]["id"] == profile["id"]
                assert client.get("/api/v2/topics").json()["plans"] == []
                updated = client.post("/api/v2/profiles/" + profile["id"], json={**profile_fields, "goal": "完成成品", "version": 1})
                assert updated.status_code == 200 and updated.json()["version"] == 2
                assert client.post("/api/v2/profiles/" + profile["id"], json={**profile_fields, "version": 1}).status_code == 409
                material_values = {"request_id": "b" * 32, "title": "成品素材", "body": "保留的素材内容", "source": "https://example.com/source"}
                material = client.post('/api/v2/creation/materials', json=material_values).json()
                assert material['id'] == client.post('/api/v2/creation/materials', json=material_values).json()['id']
                assert client.get('/api/v2/creation/materials').status_code == 200
                assert client.get(f"/api/v2/articles/{article['id']}/creation").json()['workflows'] == []
                assistant = client.get(f"/api/v2/articles/{article['id']}/assistant")
                assert assistant.status_code == 200 and assistant.json()["turns"] == []
                assert assistant.json()["content"]["body"] == article["body"]
                metadata = client.get(f"/api/v2/articles/{article['id']}/metadata")
                assert metadata.status_code == 200 and metadata.json()["turns"] == []
                assert metadata.json()["content"]["summary"] == ""
                assert metadata.json()["content"]["body"] == article["body"]
                configuration = client.get("/api/v2/assistant/configuration").json()
                assert configuration["reuse"]["status"] == "not_requested"
                assert "AI_API_KEY" not in configuration
                settings = client.get("/api/v2/assistant/settings")
                assert settings.status_code == 200 and len(settings.json()["profiles"]) == 1
                assert "key_set" in settings.json()["image"]
                state = client.get("/api/state").json()
                assert (
                    next(p for p in state["posts"] if p["id"] == article["id"])["body"]
                    == article["body"]
                )
                assert "返回新编辑器" in client.get("/").text
                assert (
                    client.post(
                        "/api/v2/articles",
                        json={"title": "不允许写入"},
                        headers={"X-Studio-Token": "wrong"},
                    ).status_code
                    == 403
                )
            stop()
            # Persist a close habit before the next actual frozen-process restart.
            settings = data / "preview-preferences.json"
            assert not settings.exists()
            settings.write_text(json.dumps({"close_behavior": "exit"}), encoding="utf-8")
            # Simulate an interrupted request and an unacknowledged recovery command.
            task_id = "f" * 32
            stamp = "2026-01-01T00:00:00+00:00"
            snapshot = json.dumps(
                {
                    "post": {"id": article["id"]},
                    "draft": {"title": article["title"], "body": article["body"]},
                }
            )
            with sqlite3.connect(data / "studio.sqlite3") as db:
                workflow_id = 'c' * 32
                db.execute("INSERT INTO workflows(id,post_id,post_title,post_version,reference_ids,instruction,snapshot,status,stage,result,outline,outline_version,confirmed_outline_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'review','review',?,'成品确认提纲',1,1,?,?)",
                           (workflow_id, article['id'], article['title'], 1, json.dumps([material['id']]), '成品创作验收',
                            json.dumps({'post': article, 'references': [material], 'profile': None}),
                            json.dumps({'title': article['title'], 'body': '成品审阅后的正文😀', 'review': {'warnings': []}}), stamp, stamp))
                db.execute(
                    "INSERT INTO editor_assistant_turns(id,kind,target_id,message,mode,status,snapshot,created_at,updated_at) VALUES(?, 'post', ?, '成品中断恢复验证', 'discuss', 'running', ?, ?, ?)",
                    (task_id, article["id"], snapshot, stamp, stamp),
                )
                db.execute(
                    "INSERT INTO task_commands(request_id,kind,task_id,fingerprint,action,status,created_at,updated_at) VALUES(?, 'assistant', ?, ?, 'retry', 'pending', ?, ?)",
                    ("e" * 32, task_id, "0" * 64, stamp, stamp),
                )
            origin = start()
            with httpx.Client(base_url=origin, trust_env=False) as client:
                assert (
                    client.get("/api/v2/articles/" + article["id"]).json()["body"]
                    == "成品原始内容😀"
                )
                task = client.get(f"/api/v2/tasks/assistant/{task_id}").json()
                assert task["status"] == "interrupted" and task["post_id"] == article["id"]
                assert "run_token" not in task and "snapshot" not in task
                assert client.get("/api/v2/tasks?group=attention").json()["total"] == 1
                session = client.get('/api/v2/session').json()
                client.headers['X-Studio-Token'] = session['token']
                assert client.get('/api/v2/preferences').json()['close_behavior'] == 'exit'
                assert client.post('/api/v2/preferences', json={'close_behavior': 'tray'}).status_code == 200
                managed = client.post('/api/v2/articles', json={'title': '成品管理样稿', 'body': '可恢复正文'}).json()
                manage_route = f"/api/v2/articles/{managed['id']}/manage"
                for index, action in enumerate(['archive', 'trash', 'restore', 'unarchive'], 1):
                    response = client.post(manage_route, json={'action': action, 'version': index})
                    assert response.status_code == 200, response.text
                restored = client.get('/api/v2/articles/' + managed['id']).json()
                assert restored['body'] == '可恢复正文' and restored['version'] == 5
                route = f"/api/v2/articles/{article['id']}/creation/{workflow_id}"
                workflow = client.get(route).json()
                assert workflow['status'] == 'review' and workflow['references'][0]['id'] == material['id']
                adopt_values = {'version': 1, 'result_signature': workflow['result_signature'], 'resolution': 'unchanged'}
                adopted = client.post(route + '/adopt', json=adopt_values)
                assert adopted.status_code == 200, adopted.text
                assert adopted.json()['post']['body'] == '成品审阅后的正文😀'
                assert client.post(route + '/adopt', json=adopt_values).json()['post']['version'] == 2
                image_route = f"/api/v2/articles/{article['id']}/illustrations"
                image_plan = client.post(image_route, json={'request_id': '9' * 32, 'post_version': 2}).json()
                assert image_plan['pages'][0]['slot'] == 'cover'
                assert 'context' not in image_plan and 'snapshot' not in image_plan
                image_route += '/' + image_plan['id']
                stream = io.BytesIO()
                Image.new('RGB', (64, 64), 'teal').save(stream, format='PNG')
                uploaded = client.post('/api/v2/illustrations/upload', json={'data': base64.b64encode(stream.getvalue()).decode(), 'title': '成品合成原图'}).json()
                imported = client.post(image_route + '/import-original', json={'request_id': '8' * 32, 'version': 1, 'page_index': 0, 'image_url': uploaded['url']})
                assert imported.status_code == 200, imported.text
                image_values = {'candidate_id': '8' * 32, 'post_version': 2}
                assert client.post(image_route + '/preview', json=image_values).status_code == 200
                assert client.get('/api/v2/articles/' + article['id']).json()['version'] == 2
                image_result = client.post(image_route + '/adopt', json=image_values)
                assert image_result.status_code == 200, image_result.text
                assert image_result.json()['post']['cover'] == uploaded['url']
                assert client.post(image_route + '/adopt', json=image_values).json()['post']['version'] == 3
                assert client.get(uploaded['url']).content == stream.getvalue()
                disposable = client.post('/api/v2/articles', json={'title': '成品永久删除隔离稿', 'cover': uploaded['url']}).json()
                disposable_route = '/api/v2/articles/' + disposable['id'] + '/manage'
                assert client.post(disposable_route, json={'action': 'trash', 'version': 1}).status_code == 200
                purged = client.post(disposable_route, json={'action': 'purge', 'version': 2})
                assert purged.status_code == 200, purged.text
                assert purged.json()['protected_files'] == 1
                assert client.get('/api/v2/articles/' + disposable['id']).status_code == 404
                assert client.get(uploaded['url']).content == stream.getvalue()
                assert client.post('/api/v2/articles/trash-storage/cleanup', json={}).status_code == 200
                assert client.get('/api/state').json()['jobs'] == []
                assert (
                    client.post(
                        f"/api/v2/tasks/assistant/{task_id}/commands",
                        json={
                            "request_id": "d" * 32,
                            "revision": task["revision"],
                            "action": "retry",
                        },
                        headers={"X-Studio-Token": "wrong"},
                    ).status_code
                    == 403
                )
            stop()
            with sqlite3.connect(data / "studio.sqlite3") as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert (
                    db.execute(
                        "SELECT status FROM task_commands WHERE request_id=?", ("e" * 32,)
                    ).fetchone()[0]
                    == "unknown"
                )
                assert db.execute("SELECT COUNT(*) FROM editor_assistant_turns").fetchone()[0] == 1
        finally:
            stop()
    report = {
        "status": "passed",
        "version": "0.4.0-m4.3.3-ui",
        "modules_match": modules,
        "entrypoint_matches": True,
        "resources_match": resources,
        "checks": [
            "packaged_startup",
            "worker_alive",
            "frontend",
            "save",
            "profile_create_update_version",
            "assistant_routes",
            "metadata_routes",
            "creation_material_idempotency",
            "pending_body_restart",
            "atomic_adoption_without_delivery",
            "task_routes",
            "interrupted_task_persists_without_replay",
            "unfinished_command_not_replayed",
            "isolated_cli_skips_configuration_import",
            "legacy_readback",
            "mutation_security",
            "compatibility_return_link",
            "restart",
            "close_preference_restart_and_save",
            "reversible_archive_trash_restore",
            "sqlite_integrity",
            "image_plan_public_projection",
            "original_upload_and_media_bytes",
            "image_preview_does_not_write",
            "image_atomic_adoption_idempotence",
            "permanent_delete_shared_media_protection",
        ],
        "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "installed": False,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "modules": len(modules), "resources": len(resources)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    verify(arguments.package.resolve(), arguments.output.resolve())
