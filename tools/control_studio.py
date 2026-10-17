"""Portable launcher. Uses its own Python interpreter; no agent runtime paths."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from studio.paths import default_data_dir


def health(url):
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "status"))
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(ROOT))
    args = parser.parse_args()
    data = args.data_dir.resolve()
    url = f"http://127.0.0.1:{args.port}"
    result = health(url)
    if result and (result.get("app") != "paperplane-studio" or Path(result.get("data_dir", "")).resolve() != data):
        raise SystemExit("The port belongs to another application/data directory. Use --port to choose another port.")
    if args.action == "status":
        print(json.dumps(result or {"running": False}, ensure_ascii=False))
        return 0 if result else 1
    if args.action == "stop":
        if not result:
            print("Already stopped.")
            return 0
        with urllib.request.urlopen(url + "/api/session", timeout=3) as response:
            token = json.load(response)["token"]
        req = urllib.request.Request(url + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json", "X-Studio-Token": token})
        with urllib.request.urlopen(req, timeout=5) as response:
            json.load(response)
        for _ in range(50):
            if not health(url):
                print("Stopped. Saved content and scheduled jobs are retained.")
                return 0
            time.sleep(1)
        raise SystemExit("The server is finishing an active request. Check status in a moment.")
    if not result:
        data.mkdir(parents=True, exist_ok=True)
        with open(data / "server.log", "ab") as log:
            subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--port", str(args.port), "--data-dir", str(data)], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
        for _ in range(30):
            if health(url):
                break
            time.sleep(.5)
        else:
            raise SystemExit(f"Unable to start. See {data / 'server.log'}")
    print("PaperPlane Studio: " + url)
    if not args.no_browser:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
