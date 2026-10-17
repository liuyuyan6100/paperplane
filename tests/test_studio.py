import base64
import concurrent.futures
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from run import InstanceLock
from studio import adapters, db as db_module
from studio.config import Config
from studio.db import dumps, now
from studio.markdown import render
from studio.migration import backup_bundle, import_legacy
from studio.server import Server, export_package
from studio.service import Problem, Studio
from tools.restore_studio import restore

TEST_DIR = Path(__file__).resolve().parent.parent / "test-results"
TEST_DIR.mkdir(exist_ok=True)


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Studio(self.root / "data")
        self.addCleanup(self.app.close)
        self.env_patch = patch.dict(os.environ, {"DEVTO_API_KEY": "", "AI_API_KEY": "", "AI_BASE_URL": "", "AI_MODEL": ""})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def post(self, **kwargs):
        return self.app.save_post({"title": "测试稿件", "body": "## 真实工作流\n\n保存内容并运行发布。", "summary": "一篇用于集成测试的文章", "stage": "ready", **kwargs})

    def enqueue(self, p, channels=None, **kwargs):
        return self.app.enqueue(p["id"], {"version": p["version"], "channels": channels or ["local"], "ack_warnings": True, **kwargs})["ids"][0]

    def test_full_local_workflow_persistence_and_immutable_snapshot(self):
        p = self.post(stage="idea")
        p = self.app.save_post({"version": p["version"], "stage": "draft"}, p["id"])
        p = self.app.save_post({"version": p["version"], "stage": "ready"}, p["id"])
        job = self.enqueue(p)
        changed = self.app.save_post({"version": p["version"], "body": "新正文"}, p["id"])
        self.app.tick()
        self.assertEqual(self.app.job_detail(job)["status"], "published")
        pub = self.app.db.one("SELECT snapshot FROM publications WHERE job_id=?", (job,))
        self.assertEqual(json.loads(pub["snapshot"])["body"], p["body"])
        restarted = Studio(self.root / "data")
        self.assertEqual(restarted.post(p["id"])["body"], "新正文")
        self.assertEqual(restarted.job_detail(job)["status"], "published")

    def test_revision_conflict_does_not_overwrite(self):
        p = self.post()
        self.app.save_post({"version": 1, "title": "新标题"}, p["id"])
        with self.assertRaises(Problem) as error:
            self.app.save_post({"version": 1, "title": "过期版本"}, p["id"])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.app.post(p["id"])["title"], "新标题")

    def test_preflight_blocks_empty_unready_yaml_missing_images_and_keys(self):
        p = self.post(body="", stage="draft")
        self.assertFalse(self.app.preflight(p, ["local"])["ok"])
        with self.assertRaises(Problem):
            self.enqueue(p)
        for fields in ({"body": "---\npublished: true\n---\n正文"}, {"body": "![图](missing.png)"}, {"cover": "/media/" + "f" * 32 + ".png"}):
            p = self.post(**fields)
            self.assertFalse(self.app.preflight(p, ["local"])["ok"])
        self.assertFalse(self.app.preflight(self.post(), ["devto"])["ok"])

    def test_atomic_duplicate_enqueue_from_two_requests(self):
        p = self.post()
        def send():
            try:
                return self.enqueue(p)
            except Problem:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertEqual(sum(bool(x) for x in results), 1)
        self.assertEqual(len(self.app.jobs()), 1)

    def test_schedule_due_cancel_and_restart_recovery(self):
        p = self.post()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        job = self.enqueue(p, due_at=future)
        self.assertFalse(self.app.tick())
        self.app.job_action(job, "cancel", {})
        self.assertEqual(self.app.job_detail(job)["status"], "canceled")
        new = self.enqueue(p)
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (new,))
        self.app.recover()
        self.assertEqual(self.app.job_detail(new)["status"], "queued")
        self.app.tick()
        self.assertEqual(self.app.job_detail(new)["status"], "published")

    def test_worker_executes_without_browser_or_agent(self):
        self.app.start_worker()
        job = self.enqueue(self.post())
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and self.app.job_detail(job)["status"] != "published":
            self.app.wake.set()
            time.sleep(.05)
        self.assertEqual(self.app.job_detail(job)["status"], "published")

    def test_manual_channel_handoff_and_metrics(self):
        job = self.enqueue(self.post(), ["wechat"])
        self.app.tick()
        self.assertEqual(self.app.job_detail(job)["status"], "manual")
        with self.assertRaises(Problem):
            self.app.job_action(job, "confirm", {"url": "javascript:alert(1)"})
        with self.assertRaises(Problem):
            self.app.job_action(job, "confirm", {"url": "https://juejin.cn/post/123456"})
        self.app.job_action(job, "confirm", {"url": "https://mp.weixin.qq.com/s/fixture-article"})
        self.app.job_action(job, "metrics", {"reads": 42, "likes": 5, "comments": 2, "note": "下一篇增加示例"})
        j = self.app.job_detail(job)
        self.assertEqual((j["status"], j["reads"], j["note"]), ("published", 42, "下一篇增加示例"))
        with self.assertRaises(Problem):
            self.app.job_action(job, "retry", {})

    def test_confirmation_without_link_reuses_article_url_but_not_admin_url(self):
        for channel, existing, expected in [
            ("juejin", "https://juejin.cn/spost/7690413193492004864", "https://juejin.cn/spost/7690413193492004864"),
            ("csdn", "https://editor.csdn.net/md?articleId=123", ""),
            ("xiaohongshu", "https://creator.xiaohongshu.com/new/note-manager", ""),
            ("wechat", "", ""),
        ]:
            with self.subTest(channel=channel):
                job = self.enqueue(self.post(), ["csdn"])
                with self.app.db.connect() as db:
                    db.execute("UPDATE jobs SET status='manual',channel_id=?,url=? WHERE id=?", (channel, existing, job))
                self.assertEqual(self.app.job_detail(job)["public_url"], expected)
                self.app.job_action(job, "confirm", {})
                saved = self.app.job_detail(job)
                self.assertEqual((saved["status"], saved["url"]), ("published", expected))
                self.assertIn("用户确认", saved["note"])
                if existing and not expected:
                    self.assertTrue(any(existing in e["message"] for e in saved["events"]))
                with self.assertRaises(Problem):
                    self.app.job_action(job, "confirm", {})

    def test_devto_draft_request_uses_official_endpoint_and_snapshot_config(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        p = self.post(tags=["python"])
        job = self.enqueue(p, ["devto"])
        self.app.save_settings({"channels": [{"id": "devto", "enabled": True, "publish": True}]})
        with patch("studio.adapters.request_json", return_value={"id": 123, "url": "https://dev.to/test/post", "published_at": None}) as mock:
            self.app.tick()
        args = mock.call_args.args
        self.assertEqual(args[0], "https://dev.to/api/articles")
        self.assertFalse(args[1]["article"]["published"])
        self.assertEqual(self.app.job_detail(job)["status"], "draft")
        self.assertNotIn("test-only-key", dumps(self.app.state()))

    def test_network_uncertainty_is_not_retried_without_verification(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        job = self.enqueue(self.post(), ["devto"])
        with patch("studio.adapters.devto_send", side_effect=adapters.DeliveryError("网络中断", True)):
            self.app.tick()
        self.assertEqual(self.app.job_detail(job)["status"], "uncertain")
        with self.assertRaises(Problem):
            self.app.job_action(job, "retry", {})
        with self.assertRaises(Problem):
            self.app.job_action(job, "not_received", {"verified": False})
        self.app.job_action(job, "not_received", {"verified": True})
        self.app.job_action(job, "retry", {})
        self.assertEqual(self.app.job_detail(job)["status"], "queued")

    def test_remote_restart_does_not_resend(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        job = self.enqueue(self.post(), ["devto"])
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job,))
        self.app.recover()
        self.assertEqual(self.app.job_detail(job)["status"], "uncertain")
        self.assertFalse(self.app.tick())

    def test_deleted_at_migration_preserves_existing_job_and_is_repeatable(self):
        job = self.enqueue(self.post())
        original = self.app.job_detail(job)
        with self.app.db.connect() as db:
            db.execute("ALTER TABLE jobs DROP COLUMN deleted_at")
        for _ in range(2):
            upgraded = Studio(self.app.db.data_dir)
            self.addCleanup(upgraded.close)
            migrated = upgraded.job_detail(job)
            self.assertIsNone(migrated["deleted_at"])
            for field in ("id", "status", "snapshot", "version", "due_at", "events"):
                self.assertEqual(migrated[field], original[field])
            self.assertEqual(sum(r["name"] == "deleted_at" for r in upgraded.db.rows("PRAGMA table_info(jobs)")), 1)

    def test_queued_delete_and_restore_never_send_and_are_idempotent(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        job = self.enqueue(self.post(), ["devto"])
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET note='保留备注',error='保留历史提示' WHERE id=?", (job,))
        with patch("studio.adapters.devto_send") as send:
            self.app.job_action(job, "delete", {"note": "不得覆盖旧备注"})
            deleted = self.app.job_detail(job)
            self.assertIsNotNone(deleted["deleted_at"])
            self.assertEqual(deleted["status"], "canceled")
            self.assertEqual((deleted["note"], deleted["error"]), ("保留备注", "保留历史提示"))
            self.app.job_action(job, "delete", {})
            self.assertEqual(self.app.job_detail(job), deleted)
            self.assertFalse(self.app.tick())
            self.app.recover()
            self.app.job_action(job, "restore", {})
            restored = self.app.job_detail(job)
            self.assertIsNone(restored["deleted_at"])
            self.assertEqual(restored["status"], "canceled")
            self.app.job_action(job, "restore", {})
            self.assertEqual(self.app.job_detail(job), restored)
            self.assertFalse(self.app.tick())
            send.assert_not_called()
        self.assertEqual(restored["attempts"], 0)

    def test_running_job_cannot_be_deleted_during_send(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        job = self.enqueue(self.post(), ["devto"])
        entered, release = threading.Event(), threading.Event()

        def send(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release delivery")
            return {"status": "draft", "external_id": "123", "url": "https://dev.to/test/draft", "note": "草稿已接收"}

        with patch("studio.adapters.devto_send", side_effect=send), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.app.tick)
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(Problem) as error:
                    self.app.job_action(job, "delete", {})
                self.assertEqual(error.exception.status, 409)
                self.assertIsNone(self.app.job_detail(job)["deleted_at"])
            finally:
                release.set()
            self.assertTrue(future.result(timeout=5))
        self.assertEqual(self.app.job_detail(job)["status"], "draft")

    def test_published_delete_preserves_receipt_publication_metrics_and_deduplication(self):
        p = self.post()
        job = self.enqueue(p)
        self.app.tick()
        self.app.job_action(job, "metrics", {"reads": 42, "likes": 7, "comments": 3, "note": "历史复盘"})
        before = self.app.job_detail(job)
        publication = self.app.db.one("SELECT * FROM publications WHERE job_id=?", (job,))
        self.app.job_action(job, "delete", {})
        deleted = self.app.job_detail(job)
        for field in ("status", "snapshot", "url", "external_id", "note", "error", "reads", "likes", "comments", "metrics_at"):
            self.assertEqual(deleted[field], before[field])
        self.assertEqual(self.app.db.one("SELECT * FROM publications WHERE job_id=?", (job,)), publication)
        self.assertFalse(self.app.preflight(p, ["local"])["ok"])
        with self.assertRaises(Problem):
            self.enqueue(p)
        self.assertEqual(next(j for j in self.app.state()["jobs"] if j["id"] == job)["deleted_at"], deleted["deleted_at"])
        self.app.job_action(job, "restore", {})
        self.assertEqual(self.app.job_detail(job)["status"], "published")
        self.assertFalse(self.app.tick())

    def test_deleted_jobs_block_workflow_actions_and_preserve_unresolved_receipts(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        cases = [("manual", "cancel"), ("failed", "retry"), ("manual", "confirm"),
                 ("draft", "confirm"), ("uncertain", "not_received"),
                 ("published", "metrics"), ("draft", "sync"), ("published", "sync")]
        for status, action in cases:
            with self.subTest(status=status, action=action):
                p = self.post()
                job = self.enqueue(p, ["devto"])
                with self.app.db.connect() as db:
                    db.execute("UPDATE jobs SET status=?,external_id='123',url='https://dev.to/test/existing',note='原始回执',error='旧提示' WHERE id=?", (status, job))
                self.app.job_action(job, "delete", {})
                deleted = self.app.job_detail(job)
                self.assertEqual(deleted["status"], status)
                self.assertEqual((deleted["external_id"], deleted["url"], deleted["note"], deleted["error"]),
                                 ("123", "https://dev.to/test/existing", "原始回执", "旧提示"))
                with patch("studio.adapters.devto_metrics") as sync:
                    with self.assertRaises(Problem) as error:
                        if action == "sync":
                            self.app.sync_metrics(job)
                        else:
                            self.app.job_action(job, action, {"url": "https://dev.to/test/new", "verified": True, "reads": 100})
                    self.assertEqual(error.exception.status, 409)
                    self.assertIn("恢复", str(error.exception))
                    sync.assert_not_called()
                self.assertEqual(self.app.job_detail(job), deleted)
                self.assertFalse(self.app.preflight(p, ["devto"])["ok"])
                self.app.archive(p["id"], True)
                self.assertTrue(self.app.post(p["id"])["archived"])

    def test_sync_rechecks_deleted_state_before_saving_external_result(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        job = self.enqueue(self.post(), ["devto"])
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='draft',external_id='123',url='https://dev.to/test/draft' WHERE id=?", (job,))

        def deleting_sync(*args):
            self.app.job_action(job, "delete", {})
            return {"published_at": now(), "url": "https://dev.to/test/public", "page_views_count": 100}

        with patch("studio.adapters.devto_metrics", side_effect=deleting_sync):
            with self.assertRaises(Problem) as error:
                self.app.sync_metrics(job)
        self.assertEqual(error.exception.status, 409)
        deleted = self.app.job_detail(job)
        self.assertEqual((deleted["status"], deleted["url"], deleted["reads"]), ("draft", "https://dev.to/test/draft", 0))
        self.assertEqual(len(deleted["events"]), 2)

    def test_deleted_inconsistent_states_are_excluded_from_recovery_and_worker(self):
        for status, restored_status in (("queued", "canceled"), ("running", "uncertain")):
            with self.subTest(status=status):
                job = self.enqueue(self.post())
                with self.app.db.connect() as db:
                    db.execute("UPDATE jobs SET status=?,deleted_at=? WHERE id=?", (status, now(), job))
                self.app.recover()
                self.assertEqual(self.app.job_detail(job)["status"], status)
                self.assertFalse(self.app.tick())
                self.app.job_action(job, "restore", {})
                self.assertEqual(self.app.job_detail(job)["status"], restored_status)
                self.assertFalse(self.app.tick())

    def test_batch_rejects_invalid_input_and_changed_selections_without_partial_changes(self):
        ids = [self.enqueue(self.post()) for _ in range(2)]
        original = [self.app.job_detail(job) for job in ids]
        bad_payloads = [
            {"action": "unknown", "ids": ids}, {"action": "delete", "ids": []},
            {"action": "delete", "ids": ids[0]}, {"action": "delete", "ids": [ids[0], ids[0]]},
            {"action": "delete", "ids": ["not-an-id"]}, {"action": "delete", "ids": [42]},
            {"action": "delete", "ids": [f"{n:032x}" for n in range(1001)]},
            {"action": "purge", "ids": ids}, {"action": "purge", "ids": ids, "confirmed": 1},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(Problem) as error:
                self.app.jobs_batch(payload)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(Problem) as error:
            self.app.jobs_batch({"action": "delete", "ids": [ids[0], "0" * 32]})
        self.assertEqual(error.exception.status, 404)
        self.assertEqual([self.app.job_detail(job) for job in ids], original)

        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (ids[1],))
        with self.assertRaises(Problem) as error:
            self.app.jobs_batch({"action": "delete", "ids": ids})
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.app.job_detail(ids[0]), original[0])
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='queued' WHERE id=?", (ids[1],))
        self.app.job_action(ids[0], "delete", {})
        mixed = [self.app.job_detail(job) for job in ids]
        for action in ("delete", "restore", "purge"):
            with self.subTest(action=action), self.assertRaises(Problem) as error:
                self.app.jobs_batch({"action": action, "ids": ids, "confirmed": True})
            self.assertEqual(error.exception.status, 409)
            self.assertEqual([self.app.job_detail(job) for job in ids], mixed)

    def test_batch_delete_and_restore_preserve_receipts_and_never_restart_queued_work(self):
        p = self.post()
        ids = self.app.enqueue(p["id"], {"version": p["version"], "channels": ["local", "wechat"], "ack_warnings": True})["ids"]
        self.app.tick()
        result = self.app.jobs_batch({"action": "delete", "ids": ids})
        self.assertEqual(result, {"ok": True, "action": "delete", "ids": ids, "count": 2})
        self.assertEqual([self.app.job_detail(job)["status"] for job in ids], ["published", "canceled"])
        self.assertTrue(all(self.app.job_detail(job)["deleted_at"] for job in ids))
        result = self.app.jobs_batch({"action": "restore", "ids": ids})
        self.assertEqual((result["action"], result["count"]), ("restore", 2))
        self.assertTrue(all(self.app.job_detail(job)["deleted_at"] is None for job in ids))
        self.assertEqual([self.app.job_detail(job)["status"] for job in ids], ["published", "canceled"])
        self.assertFalse(self.app.tick())
        with self.assertRaises(Problem) as error:
            self.app.jobs_batch({"action": "restore", "ids": ids})
        self.assertEqual(error.exception.status, 409)

    def test_concurrent_batch_deletions_commit_one_complete_selection(self):
        ids = [self.enqueue(self.post()) for _ in range(3)]
        barrier = threading.Barrier(2)

        def remove():
            barrier.wait(timeout=3)
            try:
                return self.app.jobs_batch({"action": "delete", "ids": ids})
            except Problem as exc:
                return exc.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: remove(), range(2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn(409, results)
        for job in ids:
            detail = self.app.job_detail(job)
            self.assertTrue(detail["deleted_at"])
            self.assertEqual(detail["status"], "canceled")
            self.assertEqual(len(detail["events"]), 2)

    def test_purge_removes_queue_data_atomically_but_preserves_articles_assets_and_dedupe(self):
        image = self.app.upload({"data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fixture").decode()})["url"]
        p = self.post(body=f"保留原稿与本地专栏\n\n![原图]({image})")
        ids = self.app.enqueue(p["id"], {"version": p["version"], "channels": ["local", "wechat"], "ack_warnings": True})["ids"]
        self.app.tick()
        self.app.tick()
        self.app.job_action(ids[1], "confirm", {"url": "https://mp.weixin.qq.com/s/fixture-published"})
        for job in ids:
            self.app.job_action(job, "metrics", {"reads": 17, "likes": 2, "comments": 1, "note": "需要彻底清理的队列备注"})
        article = self.app.post(p["id"])
        revisions = self.app.db.rows("SELECT * FROM revisions WHERE post_id=?", (p["id"],))
        publication = self.app.db.one("SELECT * FROM publications WHERE job_id=?", (ids[0],))
        assets = self.app.db.rows("SELECT * FROM assets")
        self.app.jobs_batch({"action": "delete", "ids": ids})
        deleted = [self.app.job_detail(job) for job in ids]
        with self.app.db.connect() as db:
            db.execute("CREATE TRIGGER fail_second_purge BEFORE DELETE ON jobs WHEN OLD.id='" + ids[1] + "' BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.jobs_batch({"action": "purge", "ids": ids, "confirmed": True})
        self.assertEqual([self.app.job_detail(job) for job in ids], deleted)
        self.assertEqual(self.app.db.rows("SELECT * FROM delivery_tombstones"), [])
        with self.app.db.connect() as db:
            db.execute("DROP TRIGGER fail_second_purge")
        result = self.app.jobs_batch({"action": "purge", "ids": ids, "confirmed": True})
        self.assertEqual(result, {"ok": True, "ids": ids, "count": 2, "action": "purge"})
        self.assertEqual(self.app.jobs(), [])
        self.assertEqual(self.app.db.rows("SELECT * FROM events"), [])
        self.assertEqual(self.app.post(p["id"]), article)
        self.assertEqual(self.app.db.rows("SELECT * FROM revisions WHERE post_id=?", (p["id"],)), revisions)
        self.assertEqual(self.app.db.rows("SELECT * FROM assets"), assets)
        self.assertTrue((self.app.db.data_dir / "media" / Path(image).name).is_file())
        self.assertEqual(self.app.db.one("SELECT * FROM publications WHERE job_id=?", (ids[0],)), publication)
        tombstones = self.app.db.rows("SELECT * FROM delivery_tombstones")
        self.assertEqual(len(tombstones), 2)
        self.assertEqual(set(tombstones[0]), {"job_id", "post_id", "channel_id", "version", "legacy_key", "status", "purged_at"})
        self.assertFalse(self.app.preflight(p, ["local", "wechat"])["ok"])
        with self.assertRaises(Problem):
            self.enqueue(p)
        with self.assertRaises(Problem) as error:
            self.app.job_detail(ids[0])
        self.assertEqual(error.exception.status, 404)
        restarted = Studio(self.app.db.data_dir)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.db.one("SELECT * FROM publications WHERE job_id=?", (ids[0],)), publication)
        self.assertFalse(restarted.preflight(restarted.post(p["id"]), ["local"])["ok"])

    def test_purge_only_affects_selected_ids_and_canceled_markers_allow_new_scheduling(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        posts, ids = [], []
        for status in ("draft", "uncertain", "canceled"):
            p = self.post()
            job = self.enqueue(p, ["devto"])
            posts.append(p)
            ids.append(job)
            with self.app.db.connect() as db:
                db.execute("UPDATE jobs SET status=? WHERE id=?", (status, job))
        self.app.jobs_batch({"action": "delete", "ids": ids})
        late = self.enqueue(self.post())
        self.app.job_action(late, "delete", {})
        self.app.jobs_batch({"action": "purge", "ids": ids, "confirmed": True})
        self.assertTrue(self.app.job_detail(late)["deleted_at"])
        for p in posts[:2]:
            self.assertFalse(self.app.preflight(p, ["devto"])["ok"])
            with self.assertRaises(Problem):
                self.enqueue(p, ["devto"])
        self.assertTrue(self.app.preflight(posts[2], ["devto"])["ok"])
        with patch("studio.adapters.devto_send") as send:
            new = self.enqueue(posts[2], ["devto"])
            self.assertEqual(self.app.job_detail(new)["status"], "queued")
            send.assert_not_called()

    def test_enqueue_transaction_checks_tombstone_created_after_preflight(self):
        p = self.post()
        other = Studio(self.app.db.data_dir)
        self.addCleanup(other.close)
        original_preflight = self.app.preflight

        def delayed_preflight(post, channels):
            report = original_preflight(post, channels)
            self.assertTrue(report["ok"])
            # Another connection finishes and purges a delivery after this read.
            job = other.enqueue(p["id"], {"version": p["version"], "channels": ["local"], "ack_warnings": True})["ids"][0]
            other.tick()
            other.job_action(job, "delete", {})
            other.jobs_batch({"action": "purge", "ids": [job], "confirmed": True})
            return report

        with patch.object(self.app, "preflight", side_effect=delayed_preflight):
            with self.assertRaises(Problem) as error:
                self.enqueue(p)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.app.jobs(), [])
        self.assertEqual(len(self.app.db.rows("SELECT * FROM delivery_tombstones")), 1)

    def test_publication_detach_migration_is_atomic_and_preserves_original_reader(self):
        job = self.enqueue(self.post())
        self.app.tick()
        original = self.app.db.one("SELECT * FROM publications WHERE job_id=?", (job,))
        with self.app.db.connect(immediate=True) as db:
            db.execute("ALTER TABLE publications RENAME TO publications_new_schema")
            db.execute("CREATE TABLE publications (job_id TEXT PRIMARY KEY REFERENCES jobs(id), post_id TEXT NOT NULL, snapshot TEXT NOT NULL, published_at TEXT NOT NULL)")
            db.execute("INSERT INTO publications SELECT * FROM publications_new_schema")
            db.execute("DROP TABLE publications_new_schema")
        detach = db_module._detach_publications

        def interrupt_migration(db):
            detach(db)
            raise RuntimeError("simulated interruption after table replacement")

        with patch("studio.db._detach_publications", side_effect=interrupt_migration):
            with self.assertRaises(RuntimeError):
                Studio(self.app.db.data_dir)
        self.assertEqual(self.app.db.one("SELECT * FROM publications WHERE job_id=?", (job,)), original)
        self.assertTrue(any(row["table"] == "jobs" for row in self.app.db.rows("PRAGMA foreign_key_list(publications)")))
        self.assertIsNone(self.app.db.one("SELECT name FROM sqlite_master WHERE name='publications_retained'"))
        upgraded = Studio(self.app.db.data_dir)
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.db.rows("PRAGMA foreign_key_list(publications)"), [])
        self.assertEqual(upgraded.db.one("SELECT * FROM publications WHERE job_id=?", (job,)), original)
        upgraded.job_action(job, "delete", {})
        upgraded.jobs_batch({"action": "purge", "ids": [job], "confirmed": True})
        self.assertEqual(upgraded.db.one("SELECT * FROM publications WHERE job_id=?", (job,)), original)
        self.assertEqual(upgraded.db.rows("PRAGMA foreign_key_check"), [])

    def test_purged_legacy_records_are_not_reimported(self):
        folder = self.root / "backups" / "2026-09-11"
        folder.mkdir(parents=True)
        data = {"data": {"publish": [
            {"record_id": "historical-published", "文章标题": "旧稿", "平台": "juejin", "状态": "已发布", "发布链接": "https://example.com/old"},
            {"record_id": "historical-canceled", "文章标题": "旧稿", "平台": "wechat", "状态": "待分发"},
        ]}}
        (folder / "cm-backup-20260911.json").write_text(dumps(data), encoding="utf-8")
        self.assertEqual(import_legacy(self.app, self.root)["jobs"], 2)
        ids = [job["id"] for job in self.app.jobs()]
        self.app.jobs_batch({"action": "delete", "ids": ids})
        self.app.jobs_batch({"action": "purge", "ids": ids, "confirmed": True})
        result = import_legacy(self.app, self.root)
        self.assertEqual((result["posts"], result["jobs"]), (0, 0))
        self.assertEqual(self.app.jobs(), [])
        self.assertEqual({row["legacy_key"] for row in self.app.db.rows("SELECT legacy_key FROM delivery_tombstones")},
                         {"publish:historical-published", "publish:historical-canceled"})

    def test_sync_does_not_recreate_job_purged_during_external_read(self):
        self.app.config.update({"DEVTO_API_KEY": "test-only-key"})
        job = self.enqueue(self.post(), ["devto"])
        with self.app.db.connect() as db:
            db.execute("UPDATE jobs SET status='draft',external_id='123' WHERE id=?", (job,))

        def purging_sync(*args):
            self.app.job_action(job, "delete", {})
            self.app.jobs_batch({"action": "purge", "ids": [job], "confirmed": True})
            return {"published_at": now(), "url": "https://dev.to/test/result"}

        with patch("studio.adapters.devto_metrics", side_effect=purging_sync):
            with self.assertRaises(Problem) as error:
                self.app.sync_metrics(job)
        self.assertEqual(error.exception.status, 404)
        self.assertIsNone(self.app.db.one("SELECT id FROM jobs WHERE id=?", (job,)))
        self.assertEqual(self.app.db.rows("SELECT * FROM events WHERE job_id=?", (job,)), [])

    def test_disabled_channel_fails_then_retries_original_job(self):
        p = self.post()
        job = self.enqueue(p)
        self.app.save_settings({"channels": [{"id": "local", "enabled": False}]})
        self.app.tick()
        self.assertEqual(self.app.job_detail(job)["status"], "failed")
        self.app.save_settings({"channels": [{"id": "local", "enabled": True}]})
        self.app.job_action(job, "retry", {})
        self.app.tick()
        self.assertEqual(self.app.job_detail(job)["attempts"], 2)

    def test_uploaded_images_in_export_and_complete_restore(self):
        image = self.app.upload({"data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fake-test-png").decode()})["url"]
        p = self.post(body=f"![测试]({image})")
        job = self.enqueue(p)
        package = export_package(self.app.job_detail(job), self.app.db.data_dir)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertIn("images/" + Path(image).name, archive.namelist())
            self.assertIn("images/", archive.read("article.html").decode())
        self.app.config.update({"DEVTO_API_KEY": "test-secret-never-export"})
        backup = backup_bundle(self.app)
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            self.assertNotIn("private.json", archive.namelist())
            self.assertNotIn(b"test-secret-never-export", b"".join(archive.read(n) for n in archive.namelist()))
        zip_path = self.root / "backup.zip"
        zip_path.write_bytes(backup)
        restore(zip_path, self.root / "restored")
        restored = Studio(self.root / "restored")
        self.assertEqual(restored.post(p["id"])["body"], p["body"])
        self.assertEqual(restored.job_detail(job)["status"], "canceled")
        self.assertTrue((restored.db.data_dir / "media" / Path(image).name).is_file())
        # Restore again over an existing DB creates a complete rollback backup.
        result = restore(zip_path, self.root / "restored")
        self.assertTrue(Path(result["previous_backup"]).is_file())

    def test_restore_rejects_zip_slip_and_live_data_directory(self):
        path = self.root / "unsafe.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../outside.txt", "unsafe")
        with self.assertRaises(ValueError):
            restore(path, self.root / "restore-unsafe")
        lock = InstanceLock(self.app.db.data_dir)
        try:
            with self.assertRaises(RuntimeError):
                restore(path, self.app.db.data_dir)
        finally:
            lock.close()

    def test_legacy_import_is_idempotent_and_never_runs_old_jobs(self):
        folder = self.root / "backups" / "2026-09-11"
        folder.mkdir(parents=True)
        (folder / "cm-backup-20260911.json").write_text(dumps({"data": {"ideas": [{"record_id": "i1", "标题": "旧选题", "状态": "素材"}], "publish": [{"record_id": "p1", "文章标题": "历史文章", "平台": "juejin", "状态": "待分发"}], "creds": [{"secret": "DO NOT IMPORT"}], "tasks": [{"status": "pending"}]}}), encoding="utf-8")
        result = import_legacy(self.app, self.root)
        self.assertEqual((result["posts"], result["jobs"]), (2, 1))
        again = import_legacy(self.app, self.root)
        self.assertEqual((again["posts"], again["jobs"]), (0, 0))
        self.assertFalse(self.app.tick())
        self.assertNotIn("DO NOT IMPORT", dumps(self.app.state()))

    def test_markdown_disables_script_html_and_dangerous_links(self):
        value = render('<script>alert(1)</script>\n\n[危险](javascript:alert(1))\n\n![x](data:text/html,bad)\n\n**安全**\n\n```\n<script>x</script>\n```')
        self.assertNotIn("<script>", value)
        self.assertNotIn('href="javascript', value)
        self.assertNotIn('src="data:', value)
        self.assertIn("<strong>安全</strong>", value)

    def test_credential_storage_and_blank_preservation(self):
        self.app.save_settings({"DEVTO_API_KEY": "secret-test"})
        self.app.save_settings({"DEVTO_API_KEY": ""})
        self.assertEqual(self.app.config.get("DEVTO_API_KEY"), "secret-test")
        self.assertNotIn("secret-test", dumps(self.app.settings()))
        if os.name == "nt":
            self.assertNotIn("secret-test", self.app.config.path.read_text())

    def test_invalid_times_tags_metrics_and_archive_protection(self):
        p = self.post()
        for due in ("tomorrow", "2026-01-01T12:00:00", "2020-01-01T00:00:00Z"):
            with self.assertRaises(Problem):
                self.enqueue(p, due_at=due)
        with self.assertRaises(Problem):
            self.post(tags=[1])
        job = self.enqueue(p)
        with self.assertRaises(Problem):
            self.app.archive(p["id"], True)
        self.app.tick()
        with self.assertRaises(Problem):
            self.app.job_action(job, "metrics", {"reads": -1})
        self.app.archive(p["id"], True)
        self.assertTrue(self.app.post(p["id"])["archived"])

    def test_ai_returns_proposal_without_mutating_article(self):
        p = self.post()
        self.app.config.update({"AI_BASE_URL": "http://127.0.0.1:11434/v1", "AI_MODEL": "local-test"})
        with patch("studio.adapters.request_json", return_value={"choices": [{"message": {"content": "## 建议草稿"}}]}) as mock:
            result = adapters.ai_draft(p, "扩写", self.app.config)
        self.assertEqual(result, "## 建议草稿")
        self.assertEqual(self.app.post(p["id"])["body"], p["body"])
        self.assertEqual(mock.call_args.args[0], "http://127.0.0.1:11434/v1/chat/completions")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_DIR)
        self.app = Studio(self.tmp.name)
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.tmp.cleanup()

    def request(self, path, values=None, headers=None):
        defaults = {"Content-Type": "application/json", "X-Studio-Token": self.server.token}
        request = urllib.request.Request(self.url + path, data=dumps(values).encode() if values is not None else None, headers={**defaults, **(headers or {})})
        return urllib.request.urlopen(request, timeout=3)

    def test_real_http_content_publish_and_read(self):
        with self.request("/api/posts", {"title": "HTTP 测试", "body": "## 正文\n\n真实 HTTP 流程", "summary": "概要", "stage": "ready"}) as response:
            p = json.load(response)
        with self.request(f"/api/posts/{p['id']}/publish", {"version": 1, "channels": ["local"], "ack_warnings": True}) as response:
            job = json.load(response)["ids"][0]
        self.app.tick()
        with self.request("/read/" + job) as response:
            self.assertIn("真实 HTTP 流程", response.read().decode())
            self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        with self.request("/feed") as response:
            self.assertIn("HTTP 测试", response.read().decode())

    def test_http_delete_restore_keeps_public_page_and_exposes_recycle_state(self):
        p = self.app.save_post({"title": "回收站 HTTP 测试", "body": "已发布的正文仍可阅读", "stage": "ready"})
        job = self.app.enqueue(p["id"], {"version": p["version"], "channels": ["local"], "ack_warnings": True})["ids"][0]
        self.app.tick()
        with self.request(f"/api/jobs/{job}/delete", {}) as response:
            self.assertTrue(json.load(response)["ok"])
        with self.request("/api/state") as response:
            deleted = next(j for j in json.load(response)["jobs"] if j["id"] == job)
        self.assertIsNotNone(deleted["deleted_at"])
        self.assertEqual(deleted["status"], "published")
        with self.request(f"/api/jobs/{job}") as response:
            self.assertEqual(json.load(response)["deleted_at"], deleted["deleted_at"])
        with self.request("/read/" + job) as response:
            self.assertIn("已发布的正文仍可阅读", response.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(f"/api/jobs/{job}/metrics", {"reads": 1})
        self.assertEqual(error.exception.code, 409)
        with self.request(f"/api/jobs/{job}/restore", {}) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertIsNone(self.app.job_detail(job)["deleted_at"])
        self.assertFalse(self.app.tick())

    def test_http_batch_purge_requires_csrf_and_confirmation_and_retains_reader(self):
        p = self.app.save_post({"title": "彻底清理队列", "body": "本地专栏内容继续保留", "stage": "ready"})
        job = self.app.enqueue(p["id"], {"version": p["version"], "channels": ["local"], "ack_warnings": True})["ids"][0]
        self.app.tick()
        with self.request("/api/jobs/batch", {"action": "delete", "ids": [job]}) as response:
            self.assertEqual(json.load(response), {"ok": True, "ids": [job], "count": 1, "action": "delete"})
        for headers in ({"X-Studio-Token": "wrong"}, {"Origin": "https://evil.example"}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/jobs/batch", {"action": "purge", "ids": [job], "confirmed": True}, headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/jobs/batch", {"action": "purge", "ids": [job]})
        self.assertEqual(error.exception.code, 400)
        self.assertTrue(self.app.job_detail(job)["deleted_at"])
        with self.request("/api/jobs/batch", {"action": "purge", "ids": [job], "confirmed": True}) as response:
            self.assertEqual(json.load(response), {"ok": True, "ids": [job], "count": 1, "action": "purge"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/jobs/" + job)
        self.assertEqual(error.exception.code, 404)
        with self.request("/api/state") as response:
            self.assertEqual(json.load(response)["jobs"], [])
        with self.request("/read/" + job) as response:
            self.assertIn("本地专栏内容继续保留", response.read().decode())
        with self.request("/feed") as response:
            self.assertIn("彻底清理队列", response.read().decode())

    def test_csrf_host_and_path_traversal_are_blocked(self):
        for headers in ({"X-Studio-Token": "wrong"}, {"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/posts", {"title": "拒绝"}, headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/data/studio.sqlite3")
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/media/../private.json")

    def test_bad_json_validation_and_missing_post(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/posts", {"title": 12})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/posts/" + "0" * 32, {"version": 1, "title": "x"})
        self.assertEqual(error.exception.code, 404)

    def test_local_model_routes_use_saved_program_and_preserve_article(self):
        import sys
        self.app.save_settings({"AI_MODE": "program", "AI_PROGRAM": sys.executable,
                                "AI_PROGRAM_ARGS": json.dumps(["-c", "import sys; print(sys.stdin.read())"]),
                                "AI_PROGRAM_OUTPUT": "text", "AI_PROGRAM_TIMEOUT": "5"})
        post = self.app.save_post({"title": "原稿", "body": "不可丢失的正文"})
        with self.request("/api/ai/test", {"AI_PROGRAM": "must-not-run.exe", "body": "DO_NOT_SEND"}) as response:
            body = json.load(response)["body"]
            self.assertIn("连接测试", body)
            self.assertNotIn("DO_NOT_SEND", body)
            self.assertNotIn(post["body"], body)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/ai/draft", {"post_id": post["id"], "instruction": "扩写"})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.app.post(post["id"])["version"], 1)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/ai/test", {}, {"X-Studio-Token": "wrong"})
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
