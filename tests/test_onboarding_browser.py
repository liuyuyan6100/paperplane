"""First-use UI flows use loopback providers and temporary data, never real accounts."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import threading
from unittest.mock import patch

from PIL import Image
import pytest
from studio.service import Studio
from studio.server import Server


@pytest.mark.parametrize("probe", ["verify_usage_guide.cjs", "verify_model_wizard.cjs", "verify_model_setup.cjs"])
def test_first_use_browser(probe):
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.reply({"data": [{"id": "fixture-model"}]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append(self.path)
            picture = io.BytesIO()
            Image.new("RGB", (64, 64), "blue").save(picture, format="PNG")
            self.reply({"data": [{"b64_json": base64.b64encode(picture.getvalue()).decode()}]}
                       if "/images/" in self.path else {"choices": [{"message": {"content": "连接成功"}}]})

    with TemporaryDirectory(prefix="paperplane-onboarding-" ) as directory, patch.dict(os.environ):
        for name in ("AI_MODE", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "MODEL_SETUP_DISMISSED"):
            os.environ.pop(name, None)
        root = Path(directory)
        app = Studio(root / "data")
        if probe == "verify_usage_guide.cjs":
            app.config.update({"MODEL_SETUP_DISMISSED": True})
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        server = Server(("127.0.0.1", 0), app)
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (provider, server)]
        for thread in threads:
            thread.start()
        try:
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}",
                "model": f"http://127.0.0.1:{provider.server_port}/v1"}), encoding="utf-8")
            result = subprocess.run(["node", str(Path(__file__).with_name(probe)), str(fixture)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=100)
            assert result.returncode == 0, result.stdout + result.stderr
            assert len(calls) == (2 if probe == "verify_model_wizard.cjs" else 0)
            assert app.state()["posts"] == []
            assert app.jobs() == []
        finally:
            for service in (server, provider):
                service.shutdown()
                service.server_close()
            for thread in threads:
                thread.join(timeout=5)
            app.close()
