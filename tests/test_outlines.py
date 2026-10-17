"""Outline confirmation is a persisted gate, including edits and concurrent work."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from studio.service import Studio, Problem
from studio.workflows import WorkflowManager
from studio.db import dumps, uid, now
from studio import profiles


class OutlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(Path(self.temp.name))
        self.addCleanup(self.app.close)
        self.manager = self.app.workflows
        self.model = Mock(return_value="## 起点\n- 已知材料，建议 200 字\n## 实践\n- 过程与配图安排")
        self.app.generate_draft = self.model
        self.post = self.app.save_post({"title": "我的实践", "body": "原始材料"})

    def create(self, **values):
        return self.manager.create({"post_id": self.post["id"], "version": self.app.post(self.post["id"])["version"], "instruction": "写实践文章", **values})

    def outline(self, **values):
        task = self.create(**values)
        self.manager.tick()
        return self.manager.get(task["id"])

    def confirm(self, task, **values):
        return self.manager.confirm(task["id"], {"outline": task["outline"], "outline_version": task["outline_version"], **values})

    def test_gate_edit_confirm_idempotence_and_adoption(self):
        task = self.create()
        with self.assertRaises(Problem):
            self.manager.confirm(task["id"], {"outline": "绕过", "outline_version": 0})
        self.manager.tick()
        task = self.manager.get(task["id"])
        self.assertEqual(task["status"], "outline_review")
        self.assertEqual(task["result"], {})
        self.assertFalse(self.manager.tick())
        with self.assertRaises(Problem):
            self.manager.apply(task["id"], {"version": 1})
        self.assertEqual(self.model.call_count, 1)
        edited = self.manager.outline_edit(task["id"], {"outline": "## 人工修改的章节\n配图：真实截图", "outline_version": 1})
        with self.assertRaises(Problem):
            self.manager.outline_edit(task["id"], {"outline": "过期编辑", "outline_version": 1})
        self.assertEqual(edited["outline_version"], 2)
        self.confirm(edited)
        self.confirm(edited)
        self.model.return_value = "正文内容"
        self.manager.tick()
        self.assertIn(edited["outline"], self.model.call_args.args[1])
        self.confirm(edited)
        self.assertFalse(self.manager.tick())
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.app.post(self.post["id"])["body"], "原始材料")
        self.manager.apply(task["id"], {"version": 1})
        self.assertEqual(self.app.post(self.post["id"])["body"], "正文内容")
        self.assertEqual(self.app.jobs(), [])

    def test_unchanged_rewrite_reuses_confirmation_and_retains_previous_result(self):
        task = self.outline()
        self.confirm(task)
        self.manager.tick()
        rewrite = self.create(parent_id=task["id"], reuse_confirmed=True)
        self.assertEqual(rewrite["confirmed_outline_version"], 1)
        self.assertEqual(rewrite["status"], "queued")
        self.assertEqual(self.manager.get(task["id"])["status"], "review")
        self.manager.tick()
        self.assertEqual(self.manager.get(rewrite["id"])["status"], "review")

    def test_rewrite_after_adoption_can_reuse_outline(self):
        task = self.outline()
        self.confirm(task)
        self.manager.tick()
        self.manager.apply(task["id"], {"version": 1})
        rewrite = self.create(parent_id=task["id"], reuse_confirmed=True)
        self.assertEqual(rewrite["status"], "queued")
        self.assertEqual(rewrite["post_version"], 2)

    def test_changed_requirements_reference_or_post_retains_outline_and_requires_confirmation(self):
        reference = self.app.save_post({"title": "参考", "body": "旧素材"})
        task = self.outline(reference_ids=[reference["id"]])
        self.confirm(task)
        changed = self.create(parent_id=task["id"], reference_ids=[reference["id"]], instruction="新的要求", reuse_confirmed=True)
        self.assertEqual(changed["status"], "outline_review")
        self.assertEqual(changed["outline"], task["outline"])
        self.app.save_post({"version": 1, "body": "新素材"}, reference["id"])
        with self.assertRaises(Problem):
            self.confirm(changed)
        fresh = self.create(parent_id=changed["id"], reference_ids=[reference["id"]], instruction="新的要求", reuse_confirmed=True)
        self.assertEqual(fresh["status"], "outline_review")
        self.confirm(fresh)
        self.app.save_post({"version": 1, "body": "新原稿"}, self.post["id"])
        newer = self.create(parent_id=fresh["id"], reference_ids=[reference["id"]], instruction="新的要求", reuse_confirmed=True)
        self.assertEqual(newer["status"], "outline_review")

    def test_old_running_generation_continues_and_reports_old_inputs(self):
        task = self.outline()
        self.confirm(task)
        entered, release = threading.Event(), threading.Event()
        def generate(*_):
            entered.set()
            release.wait(5)
            return "旧输入对应正文"
        self.model.side_effect = generate
        thread = threading.Thread(target=self.manager.tick)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.app.save_post({"version": 1, "body": "新的输入"}, self.post["id"])
            newer = self.create(parent_id=task["id"], reuse_confirmed=True)
            self.assertEqual(newer["status"], "outline_review")
        finally:
            release.set()
            thread.join(5)
        old = self.manager.get(task["id"])
        self.assertEqual(old["result"]["body"], "旧输入对应正文")
        self.assertTrue(old["input_changes"])
        self.assertEqual(self.app.post(self.post["id"])["body"], "新的输入")

    def test_outline_survives_restart_and_legacy_retry_enters_outline(self):
        task = self.outline()
        restarted = WorkflowManager(self.app)
        self.addCleanup(restarted.stop)
        self.assertEqual(restarted.get(task["id"])["outline"], task["outline"])
        self.assertFalse(restarted.tick())
        legacy = self.create()
        with self.app.db.connect() as db:
            db.execute("UPDATE workflows SET status='interrupted' WHERE id=?", (legacy["id"],))
        restarted.retry(legacy["id"])
        restarted.tick()
        self.assertEqual(restarted.get(legacy["id"])["status"], "outline_review")

    def test_profile_change_requires_reconfirmation_and_uses_new_snapshot(self):
        profile = profiles.save(self.app, {"name": "测试作者", "goal": "原目标"})
        self.app.save_post({"version": 1, "profile_id": profile["id"]}, self.post["id"])
        task = self.outline()
        profiles.save(self.app, {"version": 1, "goal": "新目标"}, profile["id"])
        with self.assertRaises(Problem):
            self.confirm(task)
        fresh = self.create(parent_id=task["id"], reuse_confirmed=True)
        self.assertEqual(fresh["status"], "outline_review")
        self.assertEqual(fresh["profile"]["goal"], "新目标")
        self.assertEqual(self.manager.get(task["id"])["profile"]["goal"], "原目标")

    def test_selected_topic_material_reaches_outline_without_unrelated_history(self):
        reference = self.app.save_post({"title": "选题素材", "body": "TOPIC_SOURCE_EVIDENCE"})
        context = {"idea": "TOPIC_ORIGINAL_IDEA", "references": [{**reference}], "history": [{"body": "UNSELECTED_HISTORY"}]}
        with self.app.db.connect() as db:
            db.execute("INSERT INTO topic_plans(id,snapshot,status,result,selected_index,selected_post_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (uid(), dumps(context), "ready", dumps([{"angle": "SELECTED_ANGLE"}]), 0, self.post["id"], now(), now()))
        task = self.outline()
        prompt = self.model.call_args.args[1]
        for expected in ("TOPIC_SOURCE_EVIDENCE", "TOPIC_ORIGINAL_IDEA", "SELECTED_ANGLE"):
            self.assertIn(expected, prompt)
        self.assertNotIn("UNSELECTED_HISTORY", prompt)
        self.app.save_post({"version": 1, "body": "更新的选题素材"}, reference["id"])
        with self.assertRaises(Problem):
            self.confirm(task)


if __name__ == "__main__":
    unittest.main()
