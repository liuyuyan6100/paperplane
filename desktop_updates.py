"""Read published desktop releases; download only after a user request."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from desktop_version import VERSION
from studio.config import Config
from release_protocol import MAX_INSTALLER, UpdateError, version, server_url, release_info


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise UpdateError("更新服务发生跳转，请检查私有更新地址；授权不会发送到其他地址")


def open_release(url, token):
    return build_opener(NoRedirect()).open(Request(url, headers={
        "User-Agent": f"PaperPlaneStudio/{VERSION}", "Accept": "application/octet-stream", "Authorization": "Bearer " + token,
    }), timeout=20)


def read_json(url, token):
    with open_release(url, token) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise UpdateError("版本信息超过大小限制")
    return json.loads(body)


def verify_installer(path, release):
    if not path.is_file() or path.stat().st_size != release["size"]:
        return False
    with path.open("rb") as file:
        if file.read(2) != b"MZ":
            return False
        file.seek(0)
        return hashlib.file_digest(file, "sha256").hexdigest() == release["sha256"]


class Updater:
    def __init__(self, data_dir, current=VERSION):
        self.directory = Path(data_dir) / "updates"
        self.current = current
        settings = Path(data_dir) / "update-settings"
        settings.mkdir(parents=True, exist_ok=True)
        self.config = Config(settings)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.release = None
        self.thread = None
        self.on_download = None
        self.state = {"enabled": True, "current_version": current, "status": "idle", "message": "启动后自动检查更新", "progress": 0, "server_url": self.config.read().get("url", ""), "key_set": bool(self.config.read().get("token"))}

    def status(self):
        with self.lock:
            return dict(self.state)

    def set(self, **values):
        with self.lock:
            self.state.update(values)

    def configure(self, values):
        with self.lock:
            if self.state["status"] in {"checking", "downloading", "installing"}:
                raise UpdateError("正在处理更新，请完成后再修改服务器配置")
            base = server_url(values.get("server_url", ""))
            old = self.config.read()
            token = values.get("token", "")
            if not token and base == old.get("url"):
                token = old.get("token", "")
            if not isinstance(token, str) or not 32 <= len(token) <= 512 or any(ord(c) < 32 or ord(c) > 126 for c in token):
                raise UpdateError("请填写有效更新授权码；更换服务器时需重新填写，不复用旧授权")
            self.config.update({"url": base, "token": token})
            self.release = None
            self.state = {"enabled": True, "current_version": self.current, "status": "idle", "progress": 0,
                          "message": "私有更新服务器已保存", "server_url": base, "key_set": True}
        return self.status()

    def start(self, action):
        with self.lock:
            if self.state["status"] in {"checking", "downloading", "installing"}:
                return self.status()
            if action not in {"check", "download"}:
                raise UpdateError("未知更新操作")
            if not self.config.read().get("url") or not self.config.read().get("token"):
                self.set(status="unconfigured", message="请先填写私有更新服务器地址和授权码")
                return self.status()
            if action == "download" and not self.release:
                raise UpdateError("请先检查新版本")
            if action == "download" and version(self.release["version"]) <= version(self.current):
                raise UpdateError("当前已是最新版本")
            self.set(status="checking" if action == "check" else "downloading", message="正在检查更新…" if action == "check" else "正在下载安装包…", progress=0)
            self.thread = threading.Thread(target=self._run, args=(action,), name="paperplane-update", daemon=True)
            self.thread.start()
            return self.status()

    def _run(self, action):
        try:
            if action == "check":
                self.check()
            else:
                self.download()
                if self.on_download:
                    self.on_download()
        except HTTPError as exc:
            if action == "check" and exc.code == 404:
                self.set(status="no_release", message="暂未发布可下载的客户端版本")
            elif exc.code in {401, 403}:
                self.set(status="error", message="更新授权无效或已撤销，请更新授权码")
            else:
                self.set(status="error", message=f"更新服务暂时不可用（HTTP {exc.code}），请稍后重试")
        except UpdateError as exc:
            self.set(status="error", message=str(exc))
        except Exception:
            self.set(status="error", message="无法连接更新服务或安装包下载失败，请稍后重试")

    def check(self):
        connection = self.config.read()
        base = server_url(connection["url"])
        info = release_info(read_json(base + "/v1/releases/latest", connection["token"]), base)
        newer = version(info["version"]) > version(self.current)
        with self.lock:
            self.release = info
            self.state.update(latest_version=info["version"], notes=info["notes"], update_available=newer, progress=0)
        if newer and verify_installer(self.directory / info["name"], info):
            self.set(status="downloaded", message="安装包已下载并校验，可打开安装向导")
        else:
            self.set(status="available" if newer else "current", message=f"发现新版本 {info['version']}" if newer else "当前已是最新版本")

    def download(self):
        with self.lock:
            info = dict(self.release)
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / info["name"]
        partial = target.with_suffix(".exe.part")
        try:
            if not verify_installer(target, info):
                total = 0
                with open_release(info["url"], self.config.read()["token"]) as response, partial.open("wb") as file:
                    while chunk := response.read(128 * 1024):
                        if self.stop.is_set():
                            raise UpdateError("下载已中止，可重新下载")
                        total += len(chunk)
                        if total > info["size"]:
                            raise UpdateError("安装包大小与发布信息不符")
                        file.write(chunk)
                        self.set(progress=int(total * 100 / info["size"]))
                if not verify_installer(partial, info):
                    raise UpdateError("安装包校验失败，请重新下载")
                os.replace(partial, target)
            self.set(status="downloaded", progress=100, message="安装包已下载并校验，可打开安装向导")
        finally:
            partial.unlink(missing_ok=True)

    def installer(self):
        with self.lock:
            info = dict(self.release or {})
        if not info or version(info["version"]) <= version(self.current):
            raise UpdateError("没有可安装的新版本")
        path = self.directory / info["name"]
        if not verify_installer(path, info):
            raise UpdateError("安装包未下载或校验失败，请重新下载")
        return path
