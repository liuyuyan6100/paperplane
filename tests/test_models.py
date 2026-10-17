import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from studio.adapters import DeliveryError
from studio.models import MAX_RESPONSE, endpoint
from studio.server import Server
from studio.service import Problem, Studio


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {key: "" for key in (
            "AI_BASE_URL", "AI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy")})
        environment.start()
        self.addCleanup(environment.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = Studio(directory.name)
        self.addCleanup(self.app.close)
        self.payload = {"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-a"}]}
        self.code = 200
        self.calls = []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                test.calls.append((self.path, self.headers.get("Authorization")))
                self.send_response(test.code)
                if test.code == 302:
                    self.send_header("Location", "/must-not-follow")
                body = test.payload if isinstance(test.payload, bytes) else json.dumps(test.payload).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.model = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.start(self.model)
        self.base = f"http://127.0.0.1:{self.model.server_port}/v1"
        self.app.save_settings({"AI_BASE_URL": self.base, "AI_API_KEY": "private-test-key", "AI_MODEL": "current-model"})
        self.private = self.app.config.path.read_bytes()
        self.server = Server(("127.0.0.1", 0), self.app)
        self.start(self.server)

    def start(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)

    def request(self, token=True):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/api/ai/models",
            data=b"{}", headers={"Content-Type": "application/json",
                                 "X-Studio-Token": self.server.token if token else ""})
        return json.load(urllib.request.urlopen(request, timeout=3))

    def test_http_catalog_uses_stored_key_without_saving_or_switching(self):
        result = self.request()
        self.assertEqual(result["models"], ["model-a", "model-b"])
        self.assertEqual(self.calls, [("/v1/models", "Bearer private-test-key")])
        self.assertNotIn("private-test-key", json.dumps(result))
        self.assertEqual(self.app.config.path.read_bytes(), self.private)
        self.assertEqual(self.app.settings()["ai_model"], "current-model")

    def test_requires_session_and_rejects_credential_reuse_at_changed_url(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(token=False)
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(Problem):
            self.app.available_models({"base_url": self.base + "/different"})
        self.assertEqual(self.calls, [])
        self.app.available_models({"base_url": self.base + "/different", "api_key": "new-key"})
        self.assertEqual(self.calls[-1], ("/v1/different/models", "Bearer new-key"))
        self.assertEqual(self.app.config.path.read_bytes(), self.private)

    def test_draft_key_and_supported_shapes_with_empty_and_invalid_entries(self):
        for payload in (["b", "a", "a", None, ""], {"models": ["b", {"id": "a"}, {}, {"id": 1}]}):
            self.payload = payload
            self.assertEqual(self.app.available_models({"api_key": "draft-key"})["models"], ["a", "b"])
            self.assertEqual(self.calls[-1][1], "Bearer draft-key")
        self.payload = {"data": []}
        self.assertEqual(self.app.available_models({})["models"], [])
        self.assertEqual(self.app.config.path.read_bytes(), self.private)

    def test_errors_are_redacted_and_redirects_are_not_followed(self):
        self.payload = b"private-test-key provider-internal-secret"
        for code in (401, 403, 404, 429, 500, 302):
            self.code = code
            self.calls.clear()
            with self.assertRaises(Problem) as error:
                self.app.available_models({})
            self.assertNotIn("private-test-key", str(error.exception))
            self.assertEqual(len(self.calls), 1)
        self.code = 200
        with self.assertRaises(Problem):
            self.app.available_models({})
        self.payload = b" " * (MAX_RESPONSE + 1)
        with self.assertRaises(Problem):
            self.app.available_models({})

    def test_rejects_bad_endpoint_key_and_catalog_shapes(self):
        for value in (None, "http://example.com/v1", "https://user:pass@example.com", "https://example.com?q=1",
                      "https://example.com/#part", "https://example.com:bad", "https://example.com/a\nb"):
            with self.assertRaises(DeliveryError):
                endpoint(value)
        with self.assertRaises(Problem):
            self.app.available_models({"api_key": "key\nheader"})
        for payload in ({"error": "oops"}, {"data": {}}, [None] * 5001):
            self.payload = payload
            with self.assertRaises(Problem):
                self.app.available_models({})


if __name__ == "__main__":
    unittest.main()
