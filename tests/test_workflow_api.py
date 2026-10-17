"""End-to-end editorial workflow tests using a real loopback model HTTP server."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studio.db import dumps
from studio.migration import backup_bundle
from studio.server import Server
from studio.service import Studio
from tools.restore_studio import restore


GENERATED_BODY = "## 接续系列\n\n根据所选前文继续讨论，拟验证的部分仍需核实。"


class WorkflowAPITests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent.parent / "test-results"
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Test configuration cannot accidentally use the user's provider or proxy.
        isolated = {key: "" for key in (
            "AI_MODE", "AI_PROGRAM", "AI_BASE_URL", "AI_MODEL", "AI_API_KEY",
            "DEVTO_API_KEY", "WECHAT_APP_ID", "WECHAT_APP_SECRET",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
        )}
        isolated.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        environment = patch.dict(os.environ, isolated)
        environment.start()
        self.addCleanup(environment.stop)
        self.received = []
        self.received_lock = threading.Lock()
        received, received_lock = self.received, self.received_lock

        class ModelHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with received_lock:
                    received.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                     "payload": payload})
                body = dumps({"choices": [{"message": {"role": "assistant", "content": GENERATED_BODY}}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.model_server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        self.model_server.daemon_threads = True
        model_thread = threading.Thread(target=self.model_server.serve_forever, daemon=True)
        model_thread.start()
        self.addCleanup(self.close_server, self.model_server, model_thread)
        self.model_config = {
            "AI_MODE": "api", "AI_BASE_URL": f"http://127.0.0.1:{self.model_server.server_port}/v1",
            "AI_MODEL": "workflow-loopback-model", "AI_API_KEY": "dummy-test-key",
        }
        self.app = Studio(self.root / "source")
        self.addCleanup(self.app.close)
        self.app.save_settings(self.model_config)
        self.server = Server(("127.0.0.1", 0), self.app)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.close_server, self.server, self.server_thread)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @staticmethod
    def close_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    def request(self, path, values=None):
        request = urllib.request.Request(
            self.url + path,
            data=dumps(values).encode("utf-8") if values is not None else None,
            headers={"Content-Type": "application/json", "X-Studio-Token": self.server.token},
        )
        with self.opener.open(request, timeout=3) as response:
            return json.load(response)

    def test_http_workflow_calls_configured_api_with_selected_context_and_stops_at_review(self):
        post = self.request("/api/posts", {"title": "系列 05", "body": "CURRENT_SELECTED_MATERIAL", "stage": "draft"})
        previous = self.request("/api/posts", {
            "title": "系列 04", "body": "PREVIOUS_SELECTED_MATERIAL", "source": "https://source.example.test/04",
        })
        self.request("/api/posts", {"title": "未选参考", "body": "UNSELECTED_CONTEXT_MUST_NOT_BE_SENT"})
        task = self.request("/api/workflows", {
            "post_id": post["id"], "version": post["version"], "reference_ids": [previous["id"]],
            "instruction": "延续前文，区分资料和拟验证计划。",
        })
        self.assertEqual(task["status"], "queued")
        self.app.start_worker()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.request("/api/workflows/" + task["id"])
            if task["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        self.assertEqual(task["status"], "outline_review", task.get("error"))
        self.assertEqual(len(self.received), 1)
        self.assertEqual(task["result"], {})
        self.request("/api/workflows/" + task["id"] + "/confirm", {"outline": "## 我修改后的结构", "outline_version": task["outline_version"]})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.request("/api/workflows/" + task["id"])
            if task["status"] not in ("queued", "running"):
                break
            time.sleep(.02)
        self.assertEqual(task["status"], "review", task.get("error"))
        self.assertEqual(task["result"]["body"], GENERATED_BODY)
        with self.received_lock:
            calls = list(self.received)
        self.assertEqual(len(calls), 2)
        self.assertIn("我修改后的结构", str(calls[1]["payload"]))
        self.assertIn("本次仅生成可编辑的 Markdown 提纲", calls[0]["payload"]["messages"][0]["content"])
        self.assertIn("本次是确认提纲后的正文生成阶段", calls[1]["payload"]["messages"][0]["content"])
        self.assertEqual(calls[0]["path"], "/v1/chat/completions")
        self.assertEqual(calls[0]["authorization"], "Bearer dummy-test-key")
        self.assertEqual(calls[0]["payload"]["model"], "workflow-loopback-model")
        prompt = "\n".join(message["content"] for message in calls[0]["payload"]["messages"])
        self.assertIn("CURRENT_SELECTED_MATERIAL", prompt)
        self.assertIn("PREVIOUS_SELECTED_MATERIAL", prompt)
        self.assertIn("https://source.example.test/04", prompt)
        self.assertNotIn("UNSELECTED_CONTEXT_MUST_NOT_BE_SENT", prompt)
        self.assertNotIn("dummy-test-key", prompt)
        self.assertEqual(self.app.post(post["id"])["body"], "CURRENT_SELECTED_MATERIAL")
        self.assertEqual(self.app.post(post["id"])["version"], 1)
        self.assertEqual(self.app.jobs(), [])
        self.assertEqual(self.app.db.rows("SELECT * FROM publications"), [])
        self.assertIsNone(task["applied_at"])

    def test_backup_restore_interrupts_queued_and_running_workflows_without_model_calls(self):
        post = self.app.save_post({"title": "恢复测试", "body": "备份中的原始素材"})
        values = {"post_id": post["id"], "version": 1, "instruction": "补充文章"}
        queued = self.app.workflows.create(values)
        running = self.app.workflows.create(values)
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE workflows SET status='running',stage='generate',attempts=1 WHERE id=?", (running["id"],))
        archive = self.root / "workflow-backup.zip"
        archive.write_bytes(backup_bundle(self.app))
        restored_path = self.root / "restored"
        self.assertTrue(restore(archive, restored_path)["restored"])
        restored = Studio(restored_path)
        self.addCleanup(restored.close)
        # Configure a working provider before startup: missing keys must not mask a resend bug.
        restored.save_settings(self.model_config)
        for workflow_id in (queued["id"], running["id"]):
            task = restored.workflows.get(workflow_id)
            self.assertEqual(task["status"], "interrupted")
            self.assertIn("备份恢复", task["error"])
        tick_completed = threading.Event()
        original_tick = restored.workflows.tick

        def observe_tick():
            result = original_tick()
            tick_completed.set()
            return result

        with patch.object(restored.workflows, "tick", side_effect=observe_tick):
            restored.start_worker()
            self.assertTrue(tick_completed.wait(3), "the restored background worker must actually run")
            restored.close()
        with self.received_lock:
            self.assertEqual(self.received, [])
        self.assertEqual(restored.workflows.get(queued["id"])["status"], "interrupted")
        self.assertEqual(restored.workflows.get(running["id"])["status"], "interrupted")
        self.assertEqual(restored.workflows.get(queued["id"])["attempts"], 0)
        self.assertEqual(restored.workflows.get(running["id"])["attempts"], 1)
        self.assertEqual(restored.post(post["id"])["body"], "备份中的原始素材")
        self.assertEqual(restored.jobs(), [])


if __name__ == "__main__":
    unittest.main()
