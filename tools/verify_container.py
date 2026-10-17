"""Smoke-test packaged web routes with temporary data, without external services."""
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_version import VERSION
from studio.service import Studio
from studio.server import Server
from studio.prompt_loader import PromptLoader


def main():
    with TemporaryDirectory(prefix="paperplane-container-smoke-") as folder:
        app = Studio(Path(folder))
        server = Server(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            def get(path):
                with opener.open(base + path, timeout=5) as response:
                    assert response.status == 200
                    return json.load(response)
            settings = get("/api/settings")
            assert settings["version"] == VERSION
            get("/api/state")
            post = app.save_post({"title": "Container smoke", "body": "Synthetic text."})
            assert next(p for p in get("/api/state")["posts"] if p["id"] == post["id"])["body"] == "Synthetic text."
            assert get("/api/health")["version"] == VERSION
            with opener.open(base + "/", timeout=5) as response:
                assert b"<html" in response.read(3000).lower()
            assert {"generate-outline", "review-draft", "extract-materials"} <= {
                item["name"] for item in app.skill_system.list_skills()}
            loader = PromptLoader(ROOT / "agents")
            assert loader.load("PERSONA") and loader.load("WORKFLOWS")
            style = ROOT / "docs/visual-styles/aiclaw-warm-engineering-v1"
            manifest = json.loads((style / "manifest.json").read_text(encoding="utf-8"))
            assert all(hashlib.sha256((style / name).read_bytes()).hexdigest() == checksum
                       for name, checksum in manifest["files"].items())
            print(json.dumps({"ok": True, "version": VERSION, "settings_and_posts": True,
                              "skills_prompts_and_style": True, "external_services_used": False}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            app.close()


if __name__ == "__main__":
    main()
