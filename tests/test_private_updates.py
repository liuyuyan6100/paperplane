import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from desktop_updates import Updater
from private_update_server import UpdateServer
from release_protocol import UpdateError
from tools.publish_desktop_release import publish


class PrivateUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'releases'
        self.package = self.root / 'PaperPlaneStudio-Setup-0.1.4.exe'
        self.package.write_bytes(b'MZ' + b'private-installer' * 1000)
        self.manifest = publish(self.store, self.package, '0.1.4', '私有发布说明')
        self.token = 'test-private-update-authorization-' + 'x' * 32
        self.server = UpdateServer(('127.0.0.1', 0), self.store, hashlib.sha256(self.token.encode()).hexdigest())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        def close():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(3)
        self.addCleanup(close)

    def request(self, path, token=None):
        return urlopen(Request(self.base + path, headers={'Authorization': 'Bearer ' + token} if token else {}), timeout=3)

    def test_anonymous_and_wrong_authorization_cannot_read_versions_or_installers(self):
        with self.request('/healthz') as response:
            self.assertEqual(json.load(response), {'ok': True})
        for path in ('/v1/releases/latest', '/v1/installers/0.1.4/' + self.package.name):
            for token in (None, 'incorrect-token-' + 'x' * 32):
                with self.assertRaises(HTTPError) as error:
                    self.request(path, token)
                self.assertEqual(error.exception.code, 401)
                self.assertNotIn(self.token, error.exception.read().decode())

    def test_real_private_server_client_check_and_verified_download_without_install(self):
        updater = Updater(self.root / 'client', current='0.1.3')
        updater.configure({'server_url': self.base, 'token': self.token})
        updater.start('check')
        updater.thread.join(5)
        self.assertEqual(updater.status()['status'], 'available')
        updater.start('download')
        updater.thread.join(5)
        self.assertEqual(updater.status()['status'], 'downloaded')
        self.assertEqual(updater.installer().read_bytes(), self.package.read_bytes())
        self.assertNotIn(self.token, json.dumps(updater.status()))
        self.server.token_hash = hashlib.sha256(b'new-authorization').hexdigest()
        updater.start('check')
        updater.thread.join(5)
        self.assertEqual(updater.status()['status'], 'error')
        self.assertIn('授权', updater.status()['message'])

    def test_publish_is_immutable_atomic_and_cannot_silently_downgrade(self):
        self.assertEqual(publish(self.store, self.package, '0.1.4', '私有发布说明'), self.manifest)
        before = (self.store / 'latest.json').read_bytes()
        with self.assertRaises(UpdateError):
            publish(self.store, self.package, '0.1.4', '改动了说明')
        older = self.root / 'PaperPlaneStudio-Setup-0.1.2.exe'
        older.write_bytes(b'MZ-old')
        with self.assertRaises(UpdateError):
            publish(self.store, older, '0.1.2', 'old')
        self.assertEqual((self.store / 'latest.json').read_bytes(), before)
        self.assertFalse((self.store / '.publish.lock').exists())

    def test_paths_are_not_an_arbitrary_private_file_server(self):
        for path in ('/../../private.json', '/v1/installers/0.1.4/private.json', '/v1/releases/latest?token=ignored'):
            with self.assertRaises(HTTPError) as error:
                self.request(path, self.token)
            self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
