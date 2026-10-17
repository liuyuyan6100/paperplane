import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from studio import profiles
from studio.db import Database
from studio.migration import backup_bundle
from studio.server import Server
from studio.service import Problem, Studio
from tools.restore_studio import restore


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Studio(self.root / "data")
        self.addCleanup(self.app.close)

    def make_profile(self, name="工程实践", **extra):
        return profiles.save(self.app, {"name": name, "goal": "分享实践", **extra})

    def test_independent_profiles_validation_and_stale_edit(self):
        a = self.make_profile()
        b = self.make_profile("产品运营", goal="产品反馈")
        updated = profiles.save(self.app, {"version": 1, "goal": "同行交流"}, a["id"])
        self.assertEqual(updated["version"], 2)
        self.assertEqual(profiles.get(self.app, b["id"])["goal"], "产品反馈")
        with self.assertRaises(Problem) as stale:
            profiles.save(self.app, {"version": 1, "goal": "覆盖"}, a["id"])
        self.assertEqual(stale.exception.status, 409)
        for values in ({"name": " "}, {"name": []}, {"name": "a", "goal": "a" * 2001}, {"name": b["name"]}):
            with self.assertRaises(Problem):
                profiles.save(self.app, values)
        self.assertEqual(len(profiles.listing(self.app)), 2)

    def test_post_binding_and_snapshot_cannot_drift_or_leak_other_profile(self):
        a = self.make_profile(goal="ORIGINAL_GOAL")
        b = self.make_profile("另一份", goal="OTHER_PROFILE_PRIVATE")
        post = self.app.save_post({"title": "文章", "body": "素材", "profile_id": a["id"]})
        task = self.app.workflows.create({"post_id": post["id"], "version": 1, "instruction": "请创作"})
        profiles.save(self.app, {"version": 1, "goal": "LATER_GOAL"}, a["id"])
        self.app.save_post({"version": 1, "profile_id": b["id"]}, post["id"])
        with patch("studio.adapters.ai_draft", return_value="根据素材创作") as model:
            self.app.workflows.tick()
        sent = model.call_args.args[1]
        self.assertIn("ORIGINAL_GOAL", sent)
        self.assertNotIn("LATER_GOAL", sent)
        self.assertNotIn("OTHER_PROFILE_PRIVATE", sent)
        detail = self.app.workflows.get(task["id"])
        self.assertEqual(detail["profile"]["version"], 1)
        self.assertEqual(detail["status"], "outline_review")
        self.assertEqual(self.app.post(post["id"])["profile_id"], b["id"])
        with self.assertRaises(Problem):
            self.app.workflows.apply(task["id"], {"version": 2})
        next_task = self.app.workflows.create({"post_id": post["id"], "version": 2})
        self.assertEqual(next_task["profile"]["id"], b["id"])

    def test_direct_api_and_program_paths_receive_profile(self):
        a = self.make_profile(goal="目标读者能够动手验证")
        post = self.app.save_post({"title": "文章", "profile_id": a["id"]})
        with patch("studio.adapters.ai_draft", return_value="正文") as api_model:
            self.app.generate_draft(post, "写正文")
        self.assertIn(a["goal"], api_model.call_args.args[1])
        with patch.object(self.app.config, "get", return_value="program"), patch("studio.local_ai.run", return_value="正文") as local_model:
            self.app.generate_draft(post, "写正文")
        self.assertIn(a["goal"], local_model.call_args.args[0])

    def test_none_and_old_tasks_never_inherit_current_profile(self):
        a = self.make_profile(goal="NOT_FOR_OLD_TASK")
        post = self.app.save_post({"title": "旧文章"})
        task = self.app.workflows.create({"post_id": post["id"], "version": 1})
        with self.app.db.connect() as db:
            context = json.loads(db.execute("SELECT snapshot FROM workflows WHERE id=?", (task["id"],)).fetchone()[0])
            context.pop("profile")
            context["post"].pop("profile_id")
            db.execute("UPDATE workflows SET snapshot=? WHERE id=?", (json.dumps(context), task["id"]))
        self.app.save_post({"version": 1, "profile_id": a["id"]}, post["id"])
        with patch("studio.adapters.ai_draft", return_value="正文") as model:
            self.app.workflows.tick()
        self.assertNotIn(a["goal"], model.call_args.args[1])
        self.assertIsNone(self.app.workflows.get(task["id"])["profile"])

    def test_binding_validation_and_partial_save_preserves_association(self):
        a = self.make_profile()
        p = self.app.save_post({"title": "文章", "profile_id": a["id"]})
        p = self.app.save_post({"version": p["version"], "body": "修改正文"}, p["id"])
        self.assertEqual(p["profile_id"], a["id"])
        for invalid in (False, 23, [], "x", "0" * 32):
            with self.assertRaises(Problem):
                self.app.save_post({"version": p["version"], "profile_id": invalid}, p["id"])
        self.assertEqual(self.app.post(p["id"])["version"], 2)
        p = self.app.save_post({"version": 2, "profile_id": None}, p["id"])
        self.assertIsNone(p["profile_id"])

    def test_existing_database_migrates_without_rewriting_posts(self):
        folder = self.root / "legacy"
        db = Database(folder)
        with db.connect() as conn:
            conn.execute("ALTER TABLE posts DROP COLUMN profile_id")
            conn.execute("DROP TABLE profiles")
            conn.execute("INSERT INTO posts(id,title,created_at,updated_at) VALUES('old','保留旧文章','before','before')")
        migrated = Database(folder)
        self.assertIsNone(migrated.one("SELECT * FROM posts WHERE id='old'")["profile_id"])
        self.assertEqual(migrated.one("SELECT * FROM posts WHERE id='old'")["updated_at"], "before")

    def test_backup_restore_retains_profiles_binding_and_task_snapshot(self):
        a = self.make_profile()
        post = self.app.save_post({"title": "文章", "profile_id": a["id"]})
        task = self.app.workflows.create({"post_id": post["id"], "version": 1})
        bundle = self.root / "backup.zip"
        bundle.write_bytes(backup_bundle(self.app))
        destination = self.root / "restored"
        restore(bundle, destination)
        app = Studio(destination)
        self.addCleanup(app.close)
        self.assertEqual(profiles.get(app, a["id"])["goal"], a["goal"])
        self.assertEqual(app.post(post["id"])["profile_id"], a["id"])
        detail = app.workflows.get(task["id"])
        self.assertEqual(detail["profile"]["id"], a["id"])
        self.assertEqual(detail["status"], "interrupted")

    def test_http_endpoints_reuse_owner_and_session_guards(self):
        server = Server(("127.0.0.1", 0), self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        base = f"http://127.0.0.1:{server.server_port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def call(path, values=None, token=True):
            req = urllib.request.Request(base + path, data=json.dumps(values).encode() if values is not None else None,
                headers={"Content-Type": "application/json", "X-Studio-Token": server.token if token else "bad"})
            with opener.open(req) as response:
                return json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            call("/api/profiles", {"name": "未授权写入"}, token=False)
        self.assertEqual(denied.exception.code, 403)
        a = call("/api/profiles", {"name": "HTTP画像", "goal": "交流"})
        self.assertEqual(call("/api/profiles/" + a["id"])["name"], a["name"])
        self.assertEqual(call("/api/state")["profiles"][0]["id"], a["id"])
        self.assertEqual(call("/api/profiles/" + a["id"], {"version": 1, "goal": "学习"})["version"], 2)


if __name__ == "__main__":
    unittest.main()
