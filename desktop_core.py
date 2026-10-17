"""Persistent, testable settings for the Windows desktop client."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def user_data_dir():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "PaperPlaneStudio"


def normalize_server_url(value):
    if not isinstance(value, str):
        raise ValueError("服务器地址格式不正确")
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("请填写服务器地址")
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("只填写服务器的首页地址，不要包含账号、路径或参数")
    if not parsed.hostname:
        raise ValueError("服务器地址格式不正确")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("服务器地址需要以 https:// 开头")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程服务器需要使用 HTTPS")
    try:
        parsed.port
    except ValueError:
        raise ValueError("服务器端口格式不正确") from None
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


DEFAULTS = {
    "schema": 1,
    "first_server_prompt_shown": False,
    "server_url": "",
    "mode": "local",
    "close_behavior": "ask",
    "autostart": False,
}


def set_windows_autostart(enabled, executable):
    """Register only the current user's packaged client for tray-only startup."""
    if os.name != "nt":
        raise RuntimeError("开机启动仅支持 Windows")
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "PaperPlaneStudio", 0, winreg.REG_SZ, f'"{Path(executable).resolve()}" --hidden')
        else:
            try:
                winreg.DeleteValue(key, "PaperPlaneStudio")
            except FileNotFoundError:
                pass


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.values = DEFAULTS.copy()
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("客户端设置格式不正确")
            for key in DEFAULTS:
                if key in data and isinstance(data[key], type(DEFAULTS[key])):
                    self.values[key] = data[key]
        if self.values["mode"] not in ("local", "server"):
            self.values["mode"] = "local"
        if self.values["close_behavior"] not in ("ask", "tray", "exit"):
            self.values["close_behavior"] = "ask"
        if self.values["server_url"]:
            try:
                self.values["server_url"] = normalize_server_url(self.values["server_url"])
            except ValueError:
                self.values["server_url"] = ""
                self.values["mode"] = "local"

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, **changes):
        for key in changes:
            if key not in DEFAULTS:
                raise KeyError(key)
        if "server_url" in changes and changes["server_url"]:
            changes["server_url"] = normalize_server_url(changes["server_url"])
        self.values.update(changes)
        self.save()
