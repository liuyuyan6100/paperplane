import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio.adapters import DeliveryError
from studio.local_ai import run, validate
from studio.service import Studio, Problem


class LocalProgramTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent.parent / "test-results"
        root.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def config(self, code, output="text", timeout="5"):
        return {"AI_MODE": "program", "AI_PROGRAM": sys.executable,
                "AI_PROGRAM_ARGS": json.dumps(["-c", code]), "AI_PROGRAM_OUTPUT": output,
                "AI_PROGRAM_TIMEOUT": timeout}

    def test_stdin_unicode_and_shell_characters_are_literal(self):
        prompt = '中文素材 & echo nope; $(whoami) "引号"\n多行'
        self.assertEqual(run(prompt, self.config("import sys; print(sys.stdin.read())"), self.root), prompt)

    def test_json_object_and_codebuddy_array(self):
        for value in ({"result": "正文"}, [{"type": "message", "content": "internal"}, {"type": "result", "result": "正文", "is_error": False}]):
            self.assertEqual(run("", self.config("print(" + repr(json.dumps(value)) + ")", "json"), self.root), "正文")

    def test_failures_do_not_become_articles(self):
        for code, output in [("raise SystemExit(2)", "text"), ("print('')", "text"), ("print('invalid json')", "json"), ("print('{\"result\":\"failure\",\"is_error\":true}')", "json"), ("print('x'*2100000)", "text")]:
            with self.subTest(code=code), self.assertRaises(DeliveryError):
                run("", self.config(code, output), self.root)

    def test_codebuddy_empty_stdout_authentication_error_is_actionable(self):
        with self.assertRaisesRegex(DeliveryError, "重新登录"):
            run("", self.config("import sys; sys.stderr.write('Authentication required. Please use /login command to sign in to your account')", "json"), self.root)

    def test_timeout_stops_process_and_cleans_workdir(self):
        with self.assertRaisesRegex(DeliveryError, "超时"):
            run("", self.config("import time; time.sleep(30)"), self.root)
        self.assertEqual(list((self.root / "ai-runs").iterdir()), [])

    def test_background_child_is_stopped_when_parent_exits(self):
        marker = self.root / "must-not-exist"
        child = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('leaked')"
        code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); print('done')"
        self.assertEqual(run("", self.config(code), self.root), "done")
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_stopping_running_program_ends_its_process_tree_and_cleans_workdir(self):
        ready = self.root / "program-ready"
        leaked = self.root / "child-must-not-finish"
        child = ("import time; from pathlib import Path; time.sleep(2); "
                 f"Path({str(leaked)!r}).write_text('leaked')")
        code = ("import subprocess,sys,time; from pathlib import Path; "
                f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
                f"Path({str(ready)!r}).write_text('ready'); time.sleep(30)")
        stop = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run, "", self.config(code, timeout="30"), self.root, stop)
            try:
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline and not future.done():
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "the real subprocess must start before cancellation")
            finally:
                stop.set()
            with self.assertRaisesRegex(DeliveryError, "已停止"):
                future.result(timeout=3)
        self.assertEqual(list((self.root / "ai-runs").iterdir()), [])
        time.sleep(2.1)
        self.assertFalse(leaked.exists(), "a descendant survived cancellation")

    def test_preexisting_stop_never_launches_program(self):
        stop = threading.Event()
        stop.set()
        with patch("studio.local_ai.subprocess.Popen") as launch:
            with self.assertRaisesRegex(DeliveryError, "已停止"):
                run("", self.config("print('must not run')"), self.root, stop_event=stop)
        launch.assert_not_called()
        self.assertFalse((self.root / "ai-runs").exists())

    def test_channel_credentials_are_removed_from_real_subprocess_environment(self):
        values = {"WECHAT_APP_SECRET": "unit-test-wechat-secret", "WECHAT_ACCESS_TOKEN": "unit-test-token",
                  "WECHAT_SSH_KEY": "unit-test-private-key-path", "wechat_custom_credential": "unit-test-custom",
                  "DEVTO_API_KEY": "unit-test-dev-secret", "AI_API_KEY": "unit-test-ai-secret",
                  "STUDIO_TEST_PROVIDER_LOGIN": "preserved-provider-login"}
        code = ("import json,os; print(json.dumps({'forbidden': [key for key in os.environ "
                "if key.upper().startswith('WECHAT_') or key.upper() in ('DEVTO_API_KEY','AI_API_KEY')], "
                "'kept': os.environ.get('STUDIO_TEST_PROVIDER_LOGIN')}))")
        with patch.dict(os.environ, values):
            result = json.loads(run("", self.config(code), self.root))
        self.assertEqual(result, {"forbidden": [], "kept": "preserved-provider-login"})

    def test_invalid_config_and_no_partial_save(self):
        for change in ({"AI_MODE": "unknown"}, {"AI_PROGRAM": "missing-program.exe"}, {"AI_PROGRAM_ARGS": "{}"}, {"AI_PROGRAM_TIMEOUT": "301"}):
            with self.subTest(change=change), self.assertRaises(DeliveryError):
                validate({**self.config(""), **change})
        app = Studio(self.root / "data")
        self.addCleanup(app.close)
        with self.assertRaises(Problem):
            app.save_settings({"AI_MODE": "program", "AI_PROGRAM": "missing.exe", "AI_MODEL": "do-not-save"})
        self.assertNotEqual(app.config.get("AI_MODEL"), "do-not-save")

    def test_generation_does_not_change_post_and_busy_guard(self):
        app = Studio(self.root / "data")
        self.addCleanup(app.close)
        app.save_settings(self.config("print('生成正文')"))
        post = app.save_post({"title": "原标题", "body": "原文"})
        self.assertEqual(app.generate_draft(post, "扩写"), "生成正文")
        self.assertEqual(app.post(post["id"])["body"], "原文")
        with app.ai_lock:
            with self.assertRaisesRegex(Problem, "正在执行"):
                app.generate_draft(post, "扩写")


if __name__ == "__main__":
    unittest.main()
