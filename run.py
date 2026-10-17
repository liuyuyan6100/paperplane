#!/usr/bin/env python3
"""Install requirements.txt, then start the local workspace with python run.py."""
import argparse
import json
import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from studio.server import Server
from studio.service import Studio
from studio.access import CloudAccess
from studio.paths import default_data_dir

ROOT = Path(__file__).resolve().parent


class InstanceLock:
    def __init__(self, data_dir):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self.file = open(Path(data_dir) / "server.lock", "a+b")
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("这个数据目录已有服务运行，请打开已有工作台") from None

    def close(self):
        self.file.close()


def main():
    parser = argparse.ArgumentParser(description="纸飞机 · 独立推文工作台")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(ROOT))
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    try:
        access = CloudAccess.from_env()
    except (ValueError, ImportError) as exc:
        parser.error(str(exc))
    if args.host not in ("127.0.0.1", "localhost") and access is None:
        parser.error("Non-loopback listening requires PAPERPLANE_PUBLIC_URL and Cloudflare Access configuration")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    args.data_dir = args.data_dir.resolve()
    try:
        lock = InstanceLock(args.data_dir)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    app = None
    try:
        app = Studio(args.data_dir)
        library_config = args.data_dir / "shared-library.json"
        if access is None and library_config.exists():
            from tools.control_library import start
            start(json.loads(library_config.read_text(encoding="utf-8"))["root"])
        server = Server((args.host, args.port), app, access=access)
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
        app.start_worker()
        url = access.origin if access else f"http://127.0.0.1:{server.server_port}"
        (args.data_dir / "server.json").write_text(json.dumps({"pid": os.getpid(), "url": url, "data_dir": str(args.data_dir)}), encoding="utf-8")
        print(f"PaperPlane Studio ready: {url}", flush=True)
        print(f"Data: {args.data_dir}", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.3)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    except OSError as exc:
        print(f"Unable to start local server: {exc}", file=sys.stderr)
        return 1
    finally:
        if app:
            app.close()
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
