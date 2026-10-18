"""Verify frozen code/assets and a synthetic save/restart in the packaged service."""

import argparse
import hashlib
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
        "studio.api.tasks",
        "studio.application.tasks",
        "studio.domain.tasks",
        "studio.infrastructure.legacy_tasks",
        "studio.infrastructure.delivery_execution",
        "studio.api.preview",
        "studio.api.articles",
        "studio.api.assistant",
        "studio.application.assistant",
        "studio.domain.assistant",
        "studio.infrastructure.legacy_assistant",
        "studio.infrastructure.configuration_reuse",
        "studio.application.articles",
        "studio.domain.articles",
        "studio.infrastructure.legacy_articles",
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
            while not marker.exists():
                assert process.poll() is None, f"packaged startup exit {process.returncode}"
                if time.monotonic() > deadline:
                    raise TimeoutError("Packaged startup did not report readiness")
                time.sleep(0.1)
            info = json.loads(marker.read_text(encoding="utf-8"))
            runtime_pid = info["pid"]
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
                assert session["version"] == "0.3.0-m3.1" and session["milestone"] == "M3"
                tasks = client.get("/api/v2/tasks").json()
                assert tasks["total"] == 0 and len(tasks["kinds"]) == 14
                client.headers["X-Studio-Token"] = session["token"]
                response = client.post(
                    "/api/v2/articles", json={"title": "成品保存验收", "body": "成品原始内容😀"}
                )
                assert response.status_code == 200
                article = response.json()
                assert article["stage"] == "draft"
                assistant = client.get(f"/api/v2/articles/{article['id']}/assistant")
                assert assistant.status_code == 200 and assistant.json()["turns"] == []
                assert assistant.json()["content"]["body"] == article["body"]
                configuration = client.get("/api/v2/assistant/configuration").json()
                assert configuration["reuse"]["status"] == "not_requested"
                assert "AI_API_KEY" not in configuration
                settings = client.get("/api/v2/assistant/settings")
                assert settings.status_code == 200 and settings.json()["profiles"] == []
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
        "version": "0.3.0-m3.1",
        "modules_match": modules,
        "entrypoint_matches": True,
        "resources_match": resources,
        "checks": [
            "packaged_startup",
            "worker_alive",
            "frontend",
            "save",
            "assistant_routes",
            "task_routes",
            "interrupted_task_persists_without_replay",
            "unfinished_command_not_replayed",
            "isolated_cli_skips_configuration_import",
            "legacy_readback",
            "mutation_security",
            "compatibility_return_link",
            "restart",
            "sqlite_integrity",
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
