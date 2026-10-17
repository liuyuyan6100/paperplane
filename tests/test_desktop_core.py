import json
import tempfile
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace

from desktop_core import Settings, normalize_server_url
from desktop import DesktopClient, LocalService, remote_reachable


class DesktopSettingsTests(unittest.TestCase):
    def test_first_launch_defaults_to_local_and_skip_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client-settings.json"
            settings = Settings(path)
            self.assertEqual(settings.values["mode"], "local")
            self.assertFalse(settings.values["first_server_prompt_shown"])
            settings.update(first_server_prompt_shown=True)
            restored = Settings(path)
            self.assertTrue(restored.values["first_server_prompt_shown"])
            self.assertEqual(restored.values["mode"], "local")

    def test_remote_url_requires_https_and_has_no_embedded_credentials(self):
        self.assertEqual(normalize_server_url(" https://example.com/ "), "https://example.com")
        self.assertEqual(normalize_server_url("http://127.0.0.1:8787"), "http://127.0.0.1:8787")
        for value in ("http://example.com", "https://a:b@example.com", "https://example.com/other", "https://example.com?token=x", "file:///tmp/a", "https://example.com:bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_server_url(value)

    def test_settings_replace_is_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client-settings.json"
            settings = Settings(path)
            settings.update(server_url="https://example.com", mode="server")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mode"], "server")
            self.assertFalse(path.with_suffix(".tmp").exists())


class LocalServiceTests(unittest.TestCase):
    def test_independent_data_directory_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            local = LocalService(Path(directory) / "local")
            try:
                local.start()
                self.assertTrue((Path(directory) / "local" / "studio.sqlite3").exists())
                self.assertTrue(remote_reachable(local.url))
                duplicate = LocalService(Path(directory) / "local")
                with self.assertRaises(RuntimeError):
                    duplicate.start()
            finally:
                local.stop()


class DesktopCloseTests(unittest.TestCase):
    def test_close_releases_ui_thread_before_waiting_for_javascript(self):
        with tempfile.TemporaryDirectory() as directory:
            client = DesktopClient(directory)
            client.settings.update(close_behavior="exit")
            ui_released = threading.Event()
            checking = threading.Event()
            destroyed = threading.Event()

            def evaluate(_script):
                checking.set()
                if not ui_released.wait(2):
                    raise RuntimeError("UI thread stayed blocked")
                return False

            client.window = SimpleNamespace(evaluate_js=evaluate, destroy=destroyed.set)
            self.assertFalse(client.on_close())
            self.assertTrue(checking.wait(1))
            first_thread = client.close_thread
            self.assertFalse(client.on_close())
            self.assertIs(client.close_thread, first_thread)
            ui_released.set()
            first_thread.join(3)
            self.assertTrue(destroyed.is_set())
            self.assertTrue(client.on_close())

    def test_dirty_draft_keeps_window_open(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            client = DesktopClient(directory)
            client.settings.update(close_behavior="exit")
            destroyed = threading.Event()
            client.window = SimpleNamespace(evaluate_js=lambda _: True, destroy=destroyed.set)
            with patch("desktop.alert") as notify:
                self.assertFalse(client.on_close())
                client.close_thread.join(3)
                notify.assert_called_once()
            self.assertFalse(destroyed.is_set())
            self.assertFalse(client.exiting)


class FallbackTests(unittest.TestCase):
    def test_unsaved_server_draft_is_stashed_before_local_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            client = DesktopClient(directory)
            client.settings.update(server_url="https://example.com", mode="server")
            client.local.server = SimpleNamespace(server_port=8787)

            class Window:
                def __init__(self):
                    self.loaded = None

                def evaluate_js(self, _script):
                    return {"supported": True, "dirty": True, "draft": {"title": "未保存标题", "body": "未保存正文", "post_id": "p1", "saved_version": 2}}

                def load_url(self, url):
                    self.loaded = url

                def set_title(self, _title):
                    pass

            client.window = Window()
            client.notice = lambda _message: None
            self.assertTrue(client.switch("local", automatic=True))
            self.assertEqual(client.settings.values["mode"], "local")
            self.assertEqual(client.window.loaded, "http://127.0.0.1:8787")
            stashes = list((Path(directory) / "draft-recovery").glob("*.json"))
            self.assertEqual(len(stashes), 1)
            content = json.loads(stashes[0].read_text(encoding="utf-8"))
            self.assertEqual(content["draft"]["body"], "未保存正文")
            self.assertEqual(content["origin"], "https://example.com")

    def test_old_server_page_blocks_fallback_to_avoid_discarding_unknown_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            client = DesktopClient(directory)
            client.settings.update(server_url="https://example.com", mode="server")
            client.window = SimpleNamespace(evaluate_js=lambda _script: {"supported": False})
            client.notice = lambda _message: None
            self.assertFalse(client.switch("local", automatic=True))
            self.assertEqual(client.settings.values["mode"], "server")
