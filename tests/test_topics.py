import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from studio import profiles
from studio.db import dumps
from studio.service import Studio, Problem
from studio.topics import FIELDS
from studio.migration import backup_bundle
from tools.restore_studio import restore


def response():
    return dumps({"candidates": [{field: (f"验证 Agent 记忆的第 {i + 1} 种角度" if field == "title" else f"{field} 的具体说明")
                                  for field in FIELDS} for i in range(3)]})


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Studio(Path(self.tmp.name) / "source")
        self.addCleanup(self.app.close)
        self.profile = profiles.save(self.app, {"name": "测试账号", "goal": "旧目标"})
        self.other = profiles.save(self.app, {"name": "另一账号", "goal": "私有其他目标"})
        self.reference = self.app.save_post({"title": "素材", "body": "旧素材证据", "profile_id": self.profile["id"]})
        self.app.save_post({"title": "其他账号历史不应进入", "profile_id": self.other["id"]})

    def create(self, **values):
        return self.app.topics.create({"profile_id": self.profile["id"], "idea": "写一篇验证记忆的文章",
                                       "reference_ids": [self.reference["id"]], **values})

    def complete(self, plan):
        with patch.object(self.app, "generate_draft", return_value=response()):
            self.assertTrue(self.app.topics.tick())
        return self.app.topics.get(plan["id"])

    def test_snapshots_are_fixed_and_unrelated_profiles_not_sent(self):
        plan = self.create()
        profiles.save(self.app, {"version": 1, "goal": "新目标"}, self.profile["id"])
        self.app.save_post({"version": 1, "body": "新素材"}, self.reference["id"])
        with patch.object(self.app, "generate_draft", return_value=response()) as model:
            self.app.topics.tick()
        post, instruction = model.call_args.args
        self.assertEqual(post["_profile_snapshot"]["goal"], "旧目标")
        self.assertIn("旧素材证据", instruction)
        for excluded in ("新目标", "新素材", "其他账号历史不应进入", "私有其他目标"):
            self.assertNotIn(excluded, instruction)
        self.assertTrue(model.call_args.kwargs["structured"])
        self.assertEqual(len(self.app.topics.get(plan["id"])["candidates"]), 3)
        self.assertEqual(self.app.jobs(), [])

    def test_selection_is_atomic_idempotent_and_keeps_body_empty(self):
        plan = self.complete(self.create())
        count = len(self.app.state()["posts"])
        result = self.app.topics.select(plan["id"], {"index": 1})
        again = self.app.topics.select(plan["id"], {"index": 1})
        self.assertEqual(result["post"]["id"], again["post"]["id"])
        self.assertEqual(len(self.app.state()["posts"]), count + 1)
        self.assertEqual(result["post"]["stage"], "idea")
        self.assertEqual(result["post"]["body"], "")
        self.assertEqual(result["post"]["profile_id"], self.profile["id"])
        self.assertEqual(self.app.post(self.reference["id"])["version"], 1)
        with self.assertRaises(Problem):
            self.app.topics.select(plan["id"], {"index": 2})

    def test_duplicate_submission_uses_one_task(self):
        first = self.create(request_id="a" * 32)
        second = self.create(request_id="a" * 32)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.app.topics.list()), 1)

    def test_bad_output_fails_without_writing_articles_then_retry_uses_snapshot(self):
        plan = self.create()
        for raw in ('{"candidates": []}', "not json", '{"candidates":[{},{},{}]}'):
            with patch.object(self.app, "generate_draft", return_value=raw):
                self.app.topics.tick()
            current = self.app.topics.get(plan["id"])
            self.assertEqual(current["status"], "failed")
            self.assertEqual(current["candidates"], [])
            self.app.topics.retry(plan["id"])
        self.assertEqual(self.complete(plan)["status"], "completed")
        self.assertEqual(len(self.app.state()["posts"]), 2)

    def test_stop_discards_late_model_result(self):
        plan = self.create()
        def interrupted(*args, **kwargs):
            self.app.topics.interrupt()
            return response()
        with patch.object(self.app, "generate_draft", side_effect=interrupted):
            self.app.topics.tick()
        self.assertEqual(self.app.topics.get(plan["id"])["status"], "interrupted")
        self.assertEqual(self.app.topics.get(plan["id"])["candidates"], [])

    def test_invalid_inputs_and_state_rejected(self):
        for values in ({"profile_id": None}, {"idea": "", "reference_ids": []}, {"reference_ids": ["bad"]},
                       {"reference_ids": [self.reference["id"]] * 2}, {"idea": "x" * 12001}):
            with self.assertRaises(Problem):
                self.create(**values)
        plan = self.create()
        for index in (True, -1, 3, "0", 0):
            with self.assertRaises(Problem):
                self.app.topics.select(plan["id"], {"index": index})

    def test_restore_preserves_results_and_interrupts_pending_jobs(self):
        completed = self.complete(self.create())
        pending = self.create()
        archive = Path(self.tmp.name) / "backup.zip"
        archive.write_bytes(backup_bundle(self.app))
        target = Path(self.tmp.name) / "restored"
        restore(archive, target)
        restored = Studio(target)
        self.addCleanup(restored.close)
        self.assertEqual(restored.topics.get(completed["id"])["candidates"], completed["candidates"])
        self.assertEqual(restored.topics.get(pending["id"])["status"], "interrupted")
        self.assertFalse(restored.topics.tick())

    def test_worker_picks_up_topic_jobs(self):
        plan = self.create()
        called = threading.Event()
        def model(*args, **kwargs):
            called.set()
            return response()
        with patch.object(self.app, "generate_draft", side_effect=model):
            self.app.start_worker()
            self.assertTrue(called.wait(3))

    def test_structured_api_and_program_prompts(self):
        self.app.config.update({"AI_BASE_URL": "https://example.test/v1", "AI_MODEL": "test", "AI_MODE": "api"})
        post = {"title": "策划", "summary": "", "body": "", "_profile_snapshot": self.profile}
        with patch("studio.adapters.request_json", return_value={"choices": [{"message": {"content": response()}}]}) as request:
            self.app.generate_draft(post, "输出选题 JSON", structured=True)
        payload = request.call_args.args[1]
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertNotIn("Markdown", payload["messages"][0]["content"])
        self.app.config.update({"AI_MODE": "program"})
        with patch("studio.local_ai.run", return_value=response()) as run:
            self.app.generate_draft(post, "输出选题 JSON", structured=True)
        self.assertIn("只返回编辑要求指定的 JSON", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
