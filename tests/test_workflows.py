import concurrent.futures
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio.adapters import DeliveryError
from studio.db import dumps
from studio.service import Problem, Studio
from studio.workflows import WorkflowManager


TEST_DIR = Path(__file__).resolve().parent.parent / "test-results"
TEST_DIR.mkdir(exist_ok=True)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name) / "data")
        self.addCleanup(self.app.close)
        self.model = Mock(return_value="## 新的正文\n\n根据资料继续分析。[待核实]")
        self.app.generate_draft = self.model
        self.manager = WorkflowManager(self.app)
        self.addCleanup(self.manager.stop)
        self.post = self.app.save_post({"title": "系列 05", "body": "作者已有的素材", "stage": "draft"})

    def create(self, **values):
        task = self.manager.create({"post_id": self.post["id"], "version": self.post["version"],
                                    "instruction": "延续系列主线，扩写成文章", **values})
        # These tests exercise the body worker/adoption boundary. Full outline gating
        # and confirmation are covered separately in test_outlines and HTTP tests.
        with self.app.db.connect() as db:
            db.execute("UPDATE workflows SET outline='测试中已确认的提纲',outline_version=1,confirmed_outline_version=1 WHERE id=?", (task["id"],))
        return self.manager.get(task["id"])

    def test_selected_context_is_snapshotted_and_only_review_is_produced(self):
        reference = self.app.save_post({"title": "系列04", "body": "已验证的插件过程", "source": "https://example.test/source"})
        unrelated = self.app.save_post({"title": "不应发送的文章", "body": "UNSELECTED_PRIVATE_MARKER"})
        task = self.create(reference_ids=[reference["id"]])
        self.app.save_post({"version": reference["version"], "body": "REFERENCE_LATER_EDIT"}, reference["id"])
        self.app.save_post({"version": self.post["version"], "body": "POST_LATER_EDIT"}, self.post["id"])
        self.assertTrue(self.manager.tick())
        generated_post, instruction = self.model.call_args.args
        self.assertEqual(generated_post["body"], "作者已有的素材")
        self.assertIn("已验证的插件过程", instruction)
        for forbidden in ("UNSELECTED_PRIVATE_MARKER", "REFERENCE_LATER_EDIT", "POST_LATER_EDIT", unrelated["id"]):
            self.assertNotIn(forbidden, instruction)
        self.assertIn("官方 benchmark 不能改称作者实测", instruction)
        self.assertIn("不要假定未提供的系列历史", instruction)
        self.assertIn("https://example.test/source", instruction)
        done = self.manager.get(task["id"])
        self.assertEqual(done["status"], "review")
        self.assertEqual([event["stage"] for event in done["events"]], ["prepare", "prepare", "generate", "review"])
        self.assertTrue(done["result"]["review"]["warnings"])
        self.assertEqual(done["result"]["sources"][1]["version"], 1)
        self.assertEqual(self.app.post(self.post["id"])["body"], "POST_LATER_EDIT")
        self.assertEqual(self.app.jobs(), [])
        self.assertEqual(self.manager.list(self.post["id"])[0]["id"], task["id"])

    def test_background_work_completes_without_browser_and_survives_new_manager(self):
        task = self.create()
        self.manager.start()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and self.manager.get(task["id"])["status"] != "review":
            time.sleep(0.01)
        self.assertEqual(self.manager.get(task["id"])["status"], "review")
        self.manager.stop()
        restarted = WorkflowManager(self.app)
        self.assertEqual(restarted.get(task["id"])["result"]["body"], self.model.return_value)
        self.assertEqual(self.model.call_count, 1)

    def test_apply_checks_snapshot_version_preserves_new_edits_and_is_idempotent(self):
        task = self.create()
        self.manager.tick()
        self.app.save_post({"version": 1, "body": "用户的新编辑"}, self.post["id"])
        for version in (1, 2):
            with self.assertRaises(Problem) as error:
                self.manager.apply(task["id"], {"version": version})
            self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.manager.get(task["id"])["status"], "review")
        self.assertEqual(self.app.post(self.post["id"])["body"], "用户的新编辑")
        task2 = self.create(version=2)
        self.manager.tick()
        adopted = self.manager.apply(task2["id"], {"version": 2})
        self.assertEqual(adopted["post"]["version"], 3)
        self.assertEqual(adopted["post"]["stage"], "draft")
        self.assertEqual(adopted["workflow"]["status"], "applied")
        repeated = self.manager.apply(task2["id"], {"version": 2})
        self.assertEqual(repeated["post"]["version"], 3)
        self.assertEqual(self.app.jobs(), [])

    def test_edit_during_apply_is_rejected_by_save_post_version_guard(self):
        task = self.create()
        self.manager.tick()
        actual_save = self.app.save_post

        def intervening_save(values, post_id):
            actual_save({"version": 1, "body": "并发修改"}, post_id)
            return actual_save(values, post_id)

        with patch.object(self.app, "save_post", side_effect=intervening_save):
            with self.assertRaises(Problem):
                self.manager.apply(task["id"], {"version": 1})
        self.assertEqual(self.app.post(self.post["id"])["body"], "并发修改")
        self.assertEqual(self.manager.get(task["id"])["status"], "review")

    def test_cancel_running_job_discards_late_result_and_retry_uses_original_snapshot(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return "过期调用返回的正文"

        self.model.side_effect = blocked
        task = self.create()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(self.manager.tick)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.manager.cancel(task["id"])["status"], "canceled")
            self.assertEqual(self.manager.retry(task["id"])["status"], "queued")
            release.set()
            running.result(3)
        self.assertEqual(self.manager.get(task["id"])["result"], {})
        self.assertEqual(self.manager.get(task["id"])["status"], "queued")
        self.model.side_effect = None
        self.model.return_value = "重试的正文"
        self.manager.tick()
        self.assertEqual(self.manager.get(task["id"])["result"]["body"], "重试的正文")
        self.assertEqual(self.manager.get(task["id"])["attempts"], 2)
        self.assertEqual(self.app.post(self.post["id"])["version"], 1)

    def test_stop_marks_running_interrupted_and_does_not_accept_late_completion(self):
        entered, release = threading.Event(), threading.Event()
        self.model.side_effect = lambda *args: (entered.set(), release.wait(3), "晚到的结果")[-1]
        task = self.create()
        self.manager.start()
        self.assertTrue(entered.wait(2))
        self.manager.stop(timeout=0)
        self.assertEqual(self.manager.get(task["id"])["status"], "interrupted")
        release.set()
        self.manager.worker.join(3)
        self.assertEqual(self.manager.get(task["id"])["status"], "interrupted")
        self.assertEqual(self.manager.get(task["id"])["result"], {})

    def test_restart_preserves_queue_but_marks_inflight_interrupted_without_reinvoking_model(self):
        running = self.create()
        queued = self.create()
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE workflows SET status='running',stage='generate' WHERE id=?", (running["id"],))
        restarted = WorkflowManager(self.app)
        self.assertEqual(restarted.get(running["id"])["status"], "interrupted")
        self.assertEqual(restarted.get(queued["id"])["status"], "queued")
        self.model.assert_not_called()
        restarted.tick()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(restarted.get(running["id"])["status"], "interrupted")

    def test_apply_recovery_confirms_committed_revision_without_writing_again(self):
        task = self.create()
        self.manager.tick()
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE workflows SET status='running',stage='apply' WHERE id=?", (task["id"],))
        saved = self.app.save_post({"version": 1, "body": self.model.return_value, "stage": "draft"}, self.post["id"])
        restarted = WorkflowManager(self.app)
        self.assertEqual(restarted.get(task["id"])["status"], "applied")
        self.assertEqual(restarted.get(task["id"])["applied_version"], saved["version"])
        self.assertEqual(restarted.apply(task["id"], {"version": 1})["post"]["version"], 2)

    def test_apply_recovery_without_commit_keeps_review_result(self):
        task = self.create()
        self.manager.tick()
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE workflows SET status='running',stage='apply' WHERE id=?", (task["id"],))
        restarted = WorkflowManager(self.app)
        self.assertEqual(restarted.get(task["id"])["status"], "review")
        self.assertEqual(restarted.get(task["id"])["result"]["body"], self.model.return_value)

    def test_missing_images_and_sources_are_review_warnings_not_fake_generation(self):
        missing = "/media/" + "a" * 32 + ".png"
        present = "/media/" + "b" * 32 + ".png"
        media = self.app.db.data_dir / "media"
        media.mkdir(exist_ok=True)
        (media / present[7:]).write_bytes(b"local test fixture")
        self.model.return_value = f"![缺失]({missing})\n![已有]({present})\n![非法](../private.png)\n[待核实]"
        task = self.create()
        self.manager.tick()
        review = self.manager.get(task["id"])["result"]["review"]
        self.assertEqual(review["missing_images"], [missing, "../private.png"])
        self.assertTrue(any("待核实" in warning for warning in review["warnings"]))

    def test_validation_rejects_unselected_invalid_or_oversized_context_before_queueing(self):
        for values in ({"version": True}, {"version": 99}, {"reference_ids": "all"},
                       {"reference_ids": [self.post["id"]]}, {"instruction": ""},
                       {"reference_ids": ["missing"]}, {"reference_ids": ["x"] * 7}):
            with self.assertRaises(Problem):
                self.create(**values)
        large = self.app.save_post({"title": "超大素材", "body": "字" * 180001})
        with self.assertRaises(Problem):
            self.create(reference_ids=[large["id"]])
        self.assertEqual(self.manager.list(), [])
        self.model.assert_not_called()

    def test_failures_are_persistent_retryable_and_generic_errors_do_not_leak_details(self):
        self.model.side_effect = RuntimeError("SECRET_PROVIDER_DETAIL")
        task = self.create()
        self.manager.tick()
        failed = self.manager.get(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("SECRET_PROVIDER_DETAIL", dumps(failed))
        self.manager.retry(task["id"])
        self.model.side_effect = DeliveryError("请检查本地程序登录")
        self.manager.tick()
        self.assertIn("登录", self.manager.get(task["id"])["error"])
        self.assertEqual(self.app.jobs(), [])

    def test_one_background_call_is_claimed_once_and_busy_model_keeps_queue(self):
        task = self.create()
        self.app.ai_lock.acquire()
        try:
            self.assertFalse(self.manager.tick())
        finally:
            self.app.ai_lock.release()
        self.assertEqual(self.manager.get(task["id"])["status"], "queued")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.manager.tick(), range(3)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.model.call_count, 1)


if __name__ == "__main__":
    unittest.main()
