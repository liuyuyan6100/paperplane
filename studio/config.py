"""Machine-local credentials. Windows DPAPI; owner-only file on POSIX.

Credentials never enter the content database, API responses, or backups.
Environment variables override credentials entered through the settings page.
"""
import base64
import ctypes
import json
import os
import threading
from pathlib import Path


def crypt(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError("无法访问本机凭据，请在设置中重新保存")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


class Config:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "private.json"
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {}
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            if envelope.get("protection") == "dpapi":
                return json.loads(crypt(base64.b64decode(envelope["value"]), True))
            return envelope.get("value", {})

    def get(self, key, default=""):
        return os.environ.get(key) or self.read().get(key, default)

    def update(self, values):
        with self.lock:
            data = self.read()
            data.update(values)
            raw = json.dumps(data, ensure_ascii=False).encode()
            envelope = {"protection": "dpapi", "value": base64.b64encode(crypt(raw)).decode()} if os.name == "nt" else {"protection": "file", "value": data}
            temporary = self.path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream)
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
