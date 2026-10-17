import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from desktop import DesktopClient
from desktop_updates import Updater, UpdateError, NoRedirect, release_info, version



class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.updater = Updater(self.temp.name, current="0.1.9")
        self.token = "private-update-token-" + "x" * 32
        self.base = "https://updates.private.example"
        self.updater.configure({"server_url": self.base, "token": self.token})
        self.content = b"MZ" + b"test-installer" * 100
        self.name = "PaperPlaneStudio-Setup-0.1.10.exe"
        self.url = self.base + f"/v1/installers/0.1.10/{self.name}"
        self.payload = {"schema": 1, "version": "0.1.10", "platform": "windows-x64", "published": True, "notes": "更新说明",
                        "installer": {"name": self.name, "size": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest()}}

    def check(self):
        with patch("desktop_updates.read_json", return_value=self.payload), patch("desktop_updates.open_release") as download:
            self.updater.start("check")
            self.updater.thread.join(3)
            download.assert_not_called()

    def test_numeric_versions_and_check_never_downloads_installer(self):
        self.assertGreater(version("0.1.10"), version("0.1.9"))
        self.check()
        self.assertEqual(self.updater.status()["status"], "available")
        self.assertEqual(self.updater.status()["notes"], "更新说明")
        self.updater.current = "0.1.10"
        self.check()
        self.assertEqual(self.updater.status()["status"], "current")
        with self.assertRaises(UpdateError):
            self.updater.start("download")

    def test_download_verifies_bytes_reuses_valid_file_and_detects_later_tampering(self):
        self.check()
        completed = Mock()
        self.updater.on_download = completed
        with patch("desktop_updates.open_release", return_value=io.BytesIO(self.content)) as download:
            self.updater.start("download")
            self.updater.thread.join(3)
            download.assert_called_once_with(self.url, self.token)
        completed.assert_called_once()
        package = self.updater.installer()
        self.assertEqual(package.read_bytes(), self.content)
        with patch("desktop_updates.open_release") as download:
            self.updater.start("download")
            self.updater.thread.join(3)
            download.assert_not_called()
        package.write_bytes(b"MZ" + b"corrupted" * 10)
        with self.assertRaises(UpdateError):
            self.updater.installer()

    def test_corrupt_truncated_oversize_and_failed_downloads_leave_no_installable_partial(self):
        for body in (self.content[:-1], self.content + b"extra", b"X" * len(self.content)):
            with self.subTest(body=len(body)):
                self.check()
                with patch("desktop_updates.open_release", return_value=io.BytesIO(body)):
                    self.updater.start("download")
                    self.updater.thread.join(3)
                self.assertEqual(self.updater.status()["status"], "error")
                self.assertFalse(list(self.updater.directory.glob("*.part")))
                with self.assertRaises(UpdateError):
                    self.updater.installer()
        self.check()
        with patch("desktop_updates.open_release", side_effect=OSError("private-token")):
            self.updater.start("download")
            self.updater.thread.join(3)
        self.assertNotIn("private-token", self.updater.status()["message"])

    def test_busy_operation_cannot_be_dispatched_twice(self):
        entered, release = threading.Event(), threading.Event()
        def read(*_args):
            entered.set()
            release.wait(2)
            return self.payload
        with patch("desktop_updates.read_json", side_effect=read) as fetch:
            self.updater.start("check")
            self.assertTrue(entered.wait(1))
            original = self.updater.thread
            self.updater.start("check")
            self.assertIs(self.updater.thread, original)
            release.set()
            original.join(3)
            self.assertEqual(fetch.call_count, 1)

    def test_manifest_cannot_choose_foreign_download_url_or_unsafe_filename(self):
        for changes in ({"published": False}, {"platform": "macos"}, {"version": "../../0.1.10"}, {"installer": {}}):
            with self.assertRaises(UpdateError):
                release_info({**self.payload, **changes}, self.base)
        payload = copy.deepcopy(self.payload)
        payload['installer']['url'] = 'https://evil.example/stolen.exe'
        self.assertEqual(release_info(payload, self.base)['url'], self.url)
        payload['installer']['sha256'] = 'bad'
        with self.assertRaises(UpdateError):
            release_info(payload, self.base)
        with self.assertRaises(UpdateError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example/a.exe')

    def test_private_configuration_is_encrypted_and_never_reused_at_changed_host(self):
        self.assertNotIn(self.token, json.dumps(self.updater.status()))
        from studio.config import Config
        self.assertEqual(Config(Path(self.temp.name) / 'update-settings').read()['token'], self.token)
        original = self.updater.config.path.read_bytes()
        with self.assertRaises(UpdateError):
            self.updater.configure({'server_url': 'https://different.example'})
        self.assertEqual(self.updater.config.path.read_bytes(), original)
        self.updater.configure({'server_url': self.base})
        self.assertEqual(self.updater.config.read()['token'], self.token)
        for url in ('http://remote.example', 'https://user:password@example.com', 'https://example.com/other', 'https://example.com?key=x'):
            with self.assertRaises(UpdateError):
                self.updater.configure({'server_url': url, 'token': self.token})

    def test_unpublished_unreachable_or_unauthorized_private_release(self):
        for code, expected in ((404, 'no_release'), (401, 'error'), (403, 'error')):
            with patch('desktop_updates.read_json', side_effect=HTTPError('url', code, '', {}, None)):
                self.updater.start('check')
                self.updater.thread.join(3)
            self.assertEqual(self.updater.status()['status'], expected)
        with patch('desktop_updates.read_json', side_effect=OSError('internal-token')):
            self.updater.start('check')
            self.updater.thread.join(3)
        self.assertNotIn('internal-token', self.updater.status()['message'])

    def test_unconfigured_client_does_not_contact_github_or_other_hosts(self):
        fresh = Updater(Path(self.temp.name) / 'unconfigured')
        with patch('desktop_updates.read_json') as request:
            self.assertEqual(fresh.start('check')['status'], 'unconfigured')
            request.assert_not_called()

    def test_install_is_blocked_for_active_task_or_unsaved_draft_and_uses_confirmed_wizard(self):
        self.check()
        with patch("desktop_updates.open_release", return_value=io.BytesIO(self.content)):
            self.updater.download()
        for active, unsaved in ((True, False), (False, True), (False, False)):
            client = DesktopClient(self.temp.name)
            client.updater = self.updater
            client.local.active_tasks = Mock(return_value=active)
            closed = threading.Event()
            client.window = SimpleNamespace(evaluate_js=lambda _: unsaved, destroy=closed.set)
            with patch("desktop.alert"), patch("desktop.sys.frozen", True, create=True), patch("desktop.subprocess.Popen") as installer:
                client.update_action("install")
                self.assertTrue(client.close_lock.acquire(timeout=3))
                client.close_lock.release()
                if active or unsaved:
                    installer.assert_not_called()
                    self.assertFalse(closed.is_set())
                    self.assertEqual(self.updater.status()["status"], "downloaded")
                else:
                    installer.assert_called_once()
                    arguments = installer.call_args.args[0]
                    self.assertTrue(arguments[1].startswith("/DIR="))
                    self.assertFalse(any("silent" in v.lower() for v in arguments))
                    self.assertTrue(closed.is_set())


class UpdateHTTPTests(unittest.TestCase):
    def setUp(self):
        from studio.server import Server
        from studio.service import Studio
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Studio(self.temp.name)
        self.addCleanup(self.app.close)
        self.updater = Updater(self.temp.name)
        self.server = Server(("127.0.0.1", 0), self.app)
        self.server.desktop_updater = self.updater
        self.server.desktop_update_action = lambda action, values=None: self.updater.configure(values) if action == 'configure' else self.updater.start(action)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        def close():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(3)
        self.addCleanup(close)

    def request(self, action=None, token=True):
        req = Request(self.url + "/api/desktop/update" + ("/" + action if action else ""), data=b"{}" if action else None,
                      headers={"Content-Type": "application/json", "X-Studio-Token": self.server.token if token else ""})
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_update_mutations_use_session_guard_and_source_service_has_no_updater(self):
        self.assertTrue(self.request()["enabled"])
        with self.assertRaises(HTTPError) as error:
            self.request("check", token=False)
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.request("unknown")
        self.assertEqual(error.exception.code, 400)
        del self.server.desktop_updater
        del self.server.desktop_update_action
        self.assertEqual(self.request(), {"enabled": False})
        with self.assertRaises(HTTPError) as error:
            self.request("check")
        self.assertEqual(error.exception.code, 404)

    def test_configure_route_preserves_private_update_key_only_for_same_server(self):
        def configure(values):
            req = Request(self.url + '/api/desktop/update/configure', data=json.dumps(values).encode(), headers={
                'Content-Type': 'application/json', 'X-Studio-Token': self.server.token})
            with urlopen(req, timeout=3) as response:
                return json.load(response)
        token = 'special-update-only-' + 'x' * 32
        result = configure({'server_url': 'https://private.example', 'token': token})
        self.assertTrue(result['key_set'])
        self.assertNotIn(token, json.dumps(result))
        self.assertTrue(configure({'server_url': 'https://private.example'})['key_set'])
        with self.assertRaises(HTTPError) as error:
            configure({'server_url': 'https://different.example'})
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
