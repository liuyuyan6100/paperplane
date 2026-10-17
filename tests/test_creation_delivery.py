"""Boundary tests for autonomous creation and WeChat delivery integration."""
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

from studio import network
from studio.adapters import DeliveryError
from studio.config import Config
from studio.server import Server
from studio.service import Studio
from studio.markdown import render

TEST_DIR = Path(__file__).resolve().parents[1] / "test-results"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6pS0AAAAASUVORK5CYII=")


class CreationDeliveryTests(unittest.TestCase):
    def setUp(self):
        TEST_DIR.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name) / "data")
        self.addCleanup(self.app.close)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {key: "" for key in ("WECHAT_APP_ID", "WECHAT_APP_SECRET", *network.DEFAULTS)}).start()
        self.app.config.update({"WECHAT_APP_ID": "test-app", "WECHAT_APP_SECRET": "test-secret"})
        self.image = self.app.upload({"data": base64.b64encode(PNG).decode(), "title": "真实素材"})["url"]

    def post(self):
        return self.app.save_post({"title": "05-系列接续", "body": "已有材料\n\n![图](" + self.image + ")", "summary": "测试摘要", "cover": self.image, "stage": "draft"})

    def mode(self, mode):
        self.app.save_settings({"channels": [{"id": "wechat", "enabled": True, "mode": mode}]})

    def enqueue(self, post):
        return self.app.enqueue(post["id"], {"version": post["version"], "channels": ["wechat"], "ack_warnings": True})["ids"][0]

    def test_generation_review_adoption_wechat_receipt(self):
        p = self.post()
        ref = self.app.save_post({"title": "04-已有结论", "body": "实测插件加载", "source": "https://example.com/source"})
        task = self.app.workflows.create({"post_id": p["id"], "version": p["version"], "reference_ids": [ref["id"]], "instruction": "承接04篇"})
        with patch.object(self.app, "generate_draft", return_value="承接插件加载的已知结论。\n\n![图](" + self.image + ")") as model:
            self.app.workflows.tick()
            outline = self.app.workflows.get(task["id"])
            self.app.workflows.confirm(task["id"], {"outline": outline["outline"], "outline_version": outline["outline_version"]})
            self.app.workflows.tick()
        self.assertIn("实测插件加载", model.call_args.args[1])
        self.assertEqual(self.app.post(p["id"])["body"], p["body"])
        self.assertEqual(self.app.jobs(), [])
        adopted = self.app.workflows.apply(task["id"], {"version": p["version"]})["post"]
        ready = self.app.save_post({"version": adopted["version"], "stage": "ready"}, p["id"])
        self.mode("api")
        job = self.enqueue(ready)
        with patch("studio.wechat.send_draft", return_value={"status": "draft", "external_id": "wechat-id", "note": "草稿与图片已核对"}) as send:
            self.app.tick()
        self.assertEqual(send.call_args.args[0]["body"], adopted["body"])
        detail = self.app.job_detail(job)
        self.assertEqual((detail["status"], detail["external_id"], detail["channel_kind"]), ("draft", "wechat-id", "wechat"))
        self.assertEqual(self.app.db.rows("SELECT * FROM publications"), [])

    def test_channel_changes_do_not_change_existing_task_delivery_mode(self):
        p = self.post()
        ready = self.app.save_post({"version": p["version"], "stage": "ready"}, p["id"])
        manual = self.enqueue(ready)
        self.mode("api")
        with patch("studio.wechat.send_draft") as send:
            self.app.tick()
            send.assert_not_called()
        self.assertEqual(self.app.job_detail(manual)["status"], "manual")
        ready = self.app.save_post({"version": ready["version"], "summary": "新摘要"}, p["id"])
        api_job = self.enqueue(ready)
        self.mode("manual")
        with patch("studio.wechat.send_draft", return_value={"status": "draft", "external_id": "id", "note": "saved"}) as send:
            self.app.tick()
            send.assert_called_once()
        self.assertEqual(self.app.job_detail(api_job)["status"], "draft")

    def test_received_draft_with_failed_verification_retains_id(self):
        p = self.post()
        p = self.app.save_post({"version": p["version"], "stage": "ready"}, p["id"])
        self.mode("api")
        job = self.enqueue(p)
        proof = {"media_id": "known-id", "image_urls": ["https://mmbiz.qpic.cn/image/0"], "thumb_media_id": "cover-id"}
        with patch("studio.wechat.send_draft", return_value={"status": "uncertain", "external_id": "known-id", "note": "读回中断", "verification": proof}):
            self.app.tick()
        self.assertEqual(self.app.job_detail(job)["external_id"], "known-id")
        self.assertEqual(self.app.job_detail(job)["snapshot"]["wechat_receipt"], proof)
        with patch("studio.wechat.verify_draft", return_value={"status": "draft", "note": "重新核对成功"}) as read, patch("studio.wechat.send_draft") as send:
            self.app.recheck_wechat(job)
            self.assertEqual(read.call_args.args[0], "known-id")
            self.assertEqual(read.call_args.kwargs["expected_receipt"], proof)
            send.assert_not_called()
        self.assertEqual(self.app.job_detail(job)["status"], "draft")

    def test_http_background_task_survives_request_connection_closing(self):
        self.app.config.update({"AI_BASE_URL": "http://127.0.0.1:1/v1", "AI_MODEL": "mock"})
        server = Server(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def generate(*_):
            entered.set()
            release.wait(4)
            return "后台独立完成的正文"

        p = self.post()
        payload = json.dumps({"post_id": p["id"], "version": p["version"], "instruction": "根据材料写作"}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/workflows", data=payload,
                                         headers={"Content-Type": "application/json", "X-Studio-Token": server.token})
        with patch.object(self.app, "generate_draft", side_effect=generate):
            self.app.start_worker()
            with urllib.request.urlopen(request, timeout=2) as response:
                task = json.loads(response.read())
            self.assertTrue(entered.wait(2))
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and self.app.workflows.get(task["id"])["status"] != "outline_review":
                time.sleep(.05)
        self.assertEqual(self.app.workflows.get(task["id"])["status"], "outline_review")
        self.assertEqual(self.app.post(p["id"])["body"], p["body"])

    def test_settings_never_return_app_secret(self):
        result = json.dumps(self.app.settings())
        self.assertNotIn("test-secret", result)
        self.assertTrue(self.app.settings()["wechat_secret_set"])
        self.app.save_settings({"WECHAT_APP_SECRET": ""})
        self.assertEqual(self.app.config.get("WECHAT_APP_SECRET"), "test-secret")


class NetworkTests(unittest.TestCase):
    def test_blank_quote_lines_keep_paragraphs_without_literal_markers(self):
        text = render("> **项目目标**\n>\n> 下一步计划\n\n```\n> literal code\n```")
        self.assertIn("<blockquote><p><strong>项目目标</strong></p><p>下一步计划</p></blockquote>", text)
        self.assertIn("&gt; literal code", text)
        self.assertNotIn("<p>&gt;</p>", text)

    def test_route_validation_rejects_ambiguous_or_external_proxy(self):
        for proxy in ("https://example.com:443", "http://127.0.0.1:12/?access_token=secret", "http://user:password@localhost:12", "socks5h://evil.example:123"):
            with self.subTest(proxy=proxy), self.assertRaises(DeliveryError):
                network.validate_route({"WECHAT_PROXY": proxy})
        network.validate_route({"WECHAT_PROXY": "socks5h://127.0.0.1:1280"})

    def test_direct_route_ignores_ambient_proxy_configuration(self):
        with patch("studio.network.urllib.request.build_opener", wraps=urllib.request.build_opener) as opener:
            network.build_wechat_opener({})
            self.assertEqual(opener.call_args.args[0].proxies, {})


if __name__ == "__main__":
    unittest.main()
