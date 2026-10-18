"""Own the preview's single data writer and both HTTP adapters."""

import json
import os
import socket
import threading
import time
from pathlib import Path

import uvicorn

from run import InstanceLock
from studio.api.preview import create_app
from studio.server import Server
from studio.service import Studio


def preview_data_dir():
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    return base / "PaperPlaneStudioPreview" / "local"


def validate_preview_data_dir(path):
    path = Path(path).resolve()
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    stable = (base / "PaperPlaneStudio").resolve()
    if path == stable or path.is_relative_to(stable) or stable.is_relative_to(path):
        raise ValueError("验证版不能使用正式版数据目录，请选择独立目录")
    return path


class PreviewService:
    def __init__(self, data_dir, frontend=None):
        self.data_dir = validate_preview_data_dir(data_dir)
        self.frontend = frontend
        self.app = self.server = self.http = self.lock = self.socket = None
        self.legacy_thread = self.thread = None
        self.configure = None

    def start(self):
        self.lock = InstanceLock(self.data_dir)
        try:
            self.app = Studio(self.data_dir)
            if self.configure:
                self.configure(self.app)
            self.server = Server(("127.0.0.1", 0), self.app)
            self.legacy_thread = threading.Thread(
                target=self.server.serve_forever,
                kwargs={"poll_interval": 0.1},
                name="preview-compatibility",
                daemon=True,
            )
            self.legacy_thread.start()
            self.socket = socket.socket()
            self.socket.bind(("127.0.0.1", 0))
            self.port = self.socket.getsockname()[1]
            asgi = create_app(
                self.app,
                f"http://127.0.0.1:{self.server.server_port}",
                self.server.token,
                self.port,
                self.frontend,
            )
            self.http = uvicorn.Server(
                uvicorn.Config(
                    asgi,
                    host="127.0.0.1",
                    port=self.port,
                    log_level="error",
                    access_log=False,
                    log_config=None,
                )
            )
            self.thread = threading.Thread(
                target=self.http.run,
                kwargs={"sockets": [self.socket]},
                name="preview-asgi",
                daemon=True,
            )
            self.thread.start()
            deadline = time.monotonic() + 15
            while not self.http.started:
                if not self.thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("验证版接口未能启动")
                time.sleep(0.03)
            self.app.start_worker()
            (self.data_dir / "preview-runtime.json").write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "url": self.url,
                        "data_dir": str(self.data_dir),
                        "version": "0.2.0-m1.1",
                    }
                ),
                encoding="utf-8",
            )
            return self.url
        except BaseException:
            self.stop()
            raise

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self):
        return self.origin + "/editor/"

    def active_tasks(self):
        if not self.app:
            return False
        return any(
            self.app.db.one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE status IN ('running','queued')"
            )["n"]
            for table in (
                "jobs",
                "workflows",
                "topic_plans",
                "repurpose_variants",
                "illustration_plans",
                "illustration_candidates",
                "editorial_reviews",
                "editor_assistant_turns",
            )
        )

    def stop(self):
        if self.http:
            self.http.should_exit = True
        if self.thread:
            self.thread.join(timeout=10)
        if self.server:
            if self.legacy_thread:
                self.server.shutdown()
            self.server.server_close()
        if self.legacy_thread:
            self.legacy_thread.join(timeout=5)
        if self.app:
            self.app.close()
        if self.socket:
            self.socket.close()
        if self.lock:
            self.lock.close()
        self.http = self.server = self.app = self.socket = self.lock = None
        self.thread = self.legacy_thread = None
