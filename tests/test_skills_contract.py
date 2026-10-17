"""技能系统契约测试（可直接 `python tests/test_skills_contract.py` 运行，也兼容 pytest）。

覆盖：技能注册、政治风险阻断且不降级、profile=None、model_route 必填、
审稿技能成功路径、异常脱敏、提纲接口与 workflows 包装层。
"""
import json
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.skills import SkillSystem  # noqa: E402
from studio.workflows import WorkflowManager, ContentRiskBlocked  # noqa: E402

SKILLS = ROOT / "skills"
ROUTE = {"task": "outline", "model": "m", "base_url": "https://example.test",
         "mode": "api", "owner_kind": "workflows", "owner_id": "w1"}
POST = {"id": "p1", "title": "测试", "body": "正文"}


class _DB:
    def now(self):
        return "2026-10-14T00:00:00Z"


class _App:
    def __init__(self, reply=None, error=None):
        self.config = {"SKILLS_ENABLED": True}
        self.db = _DB()
        self.reply, self.error, self.calls = reply, error, []

    def generate_draft(self, draft, instruction):
        self.calls.append((draft, instruction))
        if self.error:
            raise self.error
        return self.reply


def _system(**kw):
    app = _App(**kw)
    return app, SkillSystem(app, SKILLS)


class SkillContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def test_registry_has_seven_implemented_skills(self):
        _, s = _system(reply="x")
        every = sorted(i["name"] for i in s.list_skills(enabled_only=False))
        self.assertEqual(every, ["expand-section", "extract-materials", "extract-memories",
                                 "generate-outline", "plan-topics", "review-draft", "review-performance"])
        self.assertEqual(sorted(i["name"] for i in s.list_skills()), every)

    def test_political_risk_blocks_and_is_not_returned_as_outline(self):
        reply = json.dumps({"outline": "# x", "political_risk_warning": "涉及时政"},
                           ensure_ascii=False)
        app, s = _system(reply=reply)
        r = s.execute("generate-outline", post=POST, materials=[], model_route=ROUTE)
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "POLITICAL_RISK")
        self.assertIsNone(r["data"])
        self.assertEqual(len(app.calls), 1)

    def test_workflow_wrapper_raises_non_degradable_block(self):
        reply = json.dumps({"outline": "# x", "political_risk_warning": "涉及时政"},
                           ensure_ascii=False)
        app, s = _system(reply=reply)
        app.skill_system = s
        wm = WorkflowManager.__new__(WorkflowManager)
        wm.app = app
        with self.assertRaises(ContentRiskBlocked) as ctx:
            wm._generate_outline_via_skill({"instruction": ""},
                                           {"post": POST, "references": []}, ROUTE)
        self.assertIn("内容安全检查未通过", str(ctx.exception))

    def test_outline_success_without_profile_and_route_is_frozen(self):
        reply = json.dumps({"outline": "# 提纲\n## 一", "title_suggestions": ["a"]},
                           ensure_ascii=False)
        app, s = _system(reply=reply)
        r = s.execute("generate-outline", post=POST, materials=[],
                      profile=None, model_route=ROUTE)
        self.assertTrue(r["success"])
        self.assertTrue(r["data"]["outline"].startswith("# 提纲"))
        draft = app.calls[0][0]
        self.assertEqual(draft["_model_route"], ROUTE)
        self.assertIsNone(draft["_profile_snapshot"])

    def test_model_route_is_required(self):
        _, s = _system(reply="x")
        for name, kw in (("generate-outline", {"materials": []}), ("review-draft", {})):
            r = s.execute(name, post=POST, **kw)
            self.assertFalse(r["success"], name)
            self.assertEqual(r["error"]["code"], "INVALID_INPUT", name)

    def test_review_draft_success(self):
        _, s = _system(reply="审阅建议")
        r = s.execute("review-draft", post=POST, model_route=ROUTE)
        self.assertTrue(r["success"])
        self.assertEqual(r["data"]["suggestions"], "审阅建议")
        self.assertIn("reviewed_at", r["data"])

    def test_errors_are_sanitized(self):
        _, s = _system(error=RuntimeError("Authorization: Bearer sk-abcdefghij1234567890"))
        r = s.execute("review-draft", post=POST, model_route=ROUTE)
        self.assertNotIn("sk-abcdefghij", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
