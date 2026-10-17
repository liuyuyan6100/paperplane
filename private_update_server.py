"""Read-only private installer distribution, portable across Linux hosts."""
import argparse
import hashlib
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from release_protocol import UpdateError, release_info


class UpdateServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory, token_hash):
        if not re.fullmatch(r"[0-9a-f]{64}", token_hash or ""):
            raise ValueError("A SHA256 hash of the private update token is required")
        self.directory = Path(directory).resolve()
        self.token_hash = token_hash
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperPlanePrivateUpdates/1"

    def log_message(self, *_args):
        pass  # Never log credentials or request bodies.

    def respond(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz" and not parsed.query:
            return self.respond({"ok": True})
        supplied = self.headers.get("Authorization", "")
        token = supplied[7:] if supplied.startswith("Bearer ") else ""
        if not 32 <= len(token) <= 512 or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self.server.token_hash):
            return self.respond({"error": "更新下载需要有效授权"}, 401)
        if parsed.query or parsed.fragment:
            return self.respond({"error": "接口不存在"}, 404)
        root = self.server.directory
        try:
            if parsed.path == "/v1/releases/latest":
                manifest = root / "latest.json"
                if not manifest.is_file():
                    return self.respond({"error": "暂未发布版本"}, 404)
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                release_info(payload, "http://127.0.0.1")
                return self.respond(payload)
            match = re.fullmatch(r"/v1/installers/(\d{1,8}\.\d{1,8}\.\d{1,8})/(PaperPlaneStudio-Setup-[0-9.]+\.exe)", parsed.path)
            if match:
                number, name = match.groups()
                manifest = root / number / "release.json"
                if not manifest.is_file():
                    return self.respond({"error": "版本不存在"}, 404)
                info = release_info(json.loads(manifest.read_text(encoding="utf-8")), "http://127.0.0.1")
                file = (root / number / name).resolve()
                if (info["version"] != number or info["name"] != name or not file.is_relative_to(root)
                        or not file.is_file() or file.stat().st_size != info["size"]):
                    return self.respond({"error": "安装包不存在或尚未完成发布"}, 404)
                with file.open("rb") as stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(info["size"]))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    self.end_headers()
                    while chunk := stream.read(128 * 1024):
                        self.wfile.write(chunk)
                return
            self.respond({"error": "接口不存在"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, ValueError, UpdateError):
            self.respond({"error": "更新发布尚未完成，请稍后重试"}, 503)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", type=Path, default=Path("/releases"))
    args = parser.parse_args()
    server = UpdateServer((args.host, args.port), args.data_dir, os.environ.get("PAPERPLANE_UPDATE_TOKEN_SHA256", ""))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
