"""Cloud boundary tests; install requirements-cloud.txt to run these checks."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    jwt = None

from studio.access import CloudAccess
from studio.server import Server
from studio.service import Studio, Problem


@unittest.skipUnless(jwt, "Cloud tests require requirements-cloud.txt")
class CloudTests(unittest.TestCase):
    def setUp(self):
        Path("test-results").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="test-results")
        self.app = Studio(self.temp.name)
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=self.private.public_key()))
        self.access = CloudAccess("https://paperplane.example.com", "https://test.cloudflareaccess.com",
                                  "paperplane-audience", "owner@example.com", keys=keys)
        self.server = Server(("127.0.0.1", 0), self.app, access=self.access)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.app.close()
        self.temp.cleanup()

    def token(self, **changes):
        claims = dict(iss=self.access.issuer, aud=self.access.audience, email=self.access.email,
                      type="app", sub="test-owner", iat=int(time.time()), exp=int(time.time()) + 60)
        claims.update(changes)
        return jwt.encode(claims, self.private, algorithm="RS256")

    def test_sync_device_is_scoped_and_signed(self):
        with patch.dict(os.environ, {'PAPERPLANE_SYNC_AUDIENCE':'sync-audience','PAPERPLANE_SYNC_CLIENT_ID':'my-device'}):
            token=self.token(aud='sync-audience',common_name='my-device')
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'},bearer=token)[0],200)
            self.assertEqual(self.request('/api/state',bearer=token)[0],401)
            self.assertEqual(self.request('/api/posts',{'title':'forbidden'},bearer=token)[0],401)
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'},bearer=self.token())[0],401)
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'},bearer=self.token(aud='sync-audience',common_name='other-device'))[0],401)
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'},bearer=token,Host='attacker.example')[0],401)

    def test_application_device_key_is_scoped(self):
        import hashlib
        key='a'*64
        with patch.dict(os.environ,{'PAPERPLANE_SYNC_CLIENT_ID':'my-device','PAPERPLANE_SYNC_DEVICE_HASH':hashlib.sha256(key.encode()).hexdigest()}):
            headers={'X-PaperPlane-Device':'my-device','X-PaperPlane-Sync-Key':key}
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'},**headers)[0],200)
            self.assertEqual(self.request('/api/state',**headers)[0],401)
            self.assertEqual(self.request('/api/posts',{'title':'reject'},**headers)[0],401)
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'},**{**headers,'X-PaperPlane-Sync-Key':'wrong'})[0],401)
            self.assertEqual(self.request('/api/profile-sync',{'protocol':1,'op':'list'})[0],401)

    def request(self, path, value=None, bearer=None, **extra):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Host": self.access.host}
        if bearer is not None:
            headers["Cf-Access-Jwt-Assertion"] = bearer
        if value is not None:
            headers.update({"Content-Type": "application/json", "Origin": self.access.origin,
                            "X-Studio-Token": self.server.token})
        headers.update(extra)
        conn.request("GET" if value is None else "POST", path, json.dumps(value) if value is not None else None, headers)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_signed_identity_required_for_content_media_and_session(self):
        for path in ("/", "/api/state", "/api/session", "/feed", "/media/" + "a" * 32 + ".png"):
            self.assertEqual(self.request(path)[0], 401)
        for changes in ({"aud": "wiki"}, {"iss": "https://other.cloudflareaccess.com"},
                        {"email": "other@example.com"}, {"type": "service"},
                        {"exp": int(time.time()) - 60}, {"iat": int(time.time()) + 60}):
            self.assertEqual(self.request("/api/state", bearer=self.token(**changes))[0], 401)
        tampered = self.token().rsplit(".", 1)[0] + ".invalid"
        self.assertEqual(self.request("/api/state", bearer=tampered)[0], 401)
        self.assertEqual(self.request("/api/state", bearer=self.token(), Host="attacker.example")[0], 403)

    def test_cloud_save_persists_and_requires_origin_and_csrf(self):
        token = self.token()
        status, body = self.request("/api/session", bearer=token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["deployment"], "cloud")
        self.assertEqual(self.request("/", bearer=token, **{"Sec-Fetch-Site": "cross-site"})[0], 200)
        for headers in ({"Origin": "https://evil.example"}, {"Origin": ""}, {"X-Studio-Token": "wrong"},
                        {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/posts", {"title": "reject"}, token, **headers)[0], 403)
        status, body = self.request("/api/posts", {"title": "手机稿件", "body": "跨设备保存"}, token)
        self.assertEqual(status, 200)
        post = json.loads(body)
        self.assertEqual(self.app.post(post["id"])["body"], "跨设备保存")
        self.assertEqual(self.request("/api/shutdown", {}, token)[0], 403)
        self.assertEqual(self.request("/private.json", bearer=token)[0], 404)

    def test_pwa_assets_and_remote_agent_restriction(self):
        for path in ("/manifest.webmanifest", "/sw.js", "/pwa.js", "/icon-192.png", "/icon-512.png"):
            self.assertEqual(self.request(path, bearer=self.token())[0], 200)
        with patch.dict(os.environ, {"PAPERPLANE_ALLOW_PROGRAM": "0"}):
            with self.assertRaises(Problem):
                self.app.save_settings({"AI_MODE": "program", "AI_PROGRAM": "python"})
            self.assertFalse(self.app.settings()["ai_program_allowed"])


if __name__ == "__main__":
    unittest.main()
