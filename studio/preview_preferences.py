"""Local preview preferences shared by desktop and the settings API."""

import json
import os
import tempfile
import threading
from pathlib import Path

from studio.service import Problem


class PreviewPreferences:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "preview-preferences.json"
        self.lock = threading.RLock()

    def get(self):
        with self.lock:
            if not self.path.exists():
                return {"close_behavior": "ask"}
            try:
                values = json.loads(self.path.read_text(encoding="utf-8"))
                if values["close_behavior"] not in ("ask", "tray", "exit"):
                    raise ValueError
            except (ValueError, KeyError, TypeError, OSError):
                raise Problem("无法读取关闭习惯设置，请保留原文件后处理", 500) from None
            return {"close_behavior": values["close_behavior"]}

    def update(self, behavior):
        if behavior not in ("ask", "tray", "exit"):
            raise Problem("关闭行为不正确")
        with self.lock:
            self.get()  # Never replace an unreadable existing preference.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 suffix=".tmp", delete=False) as file:
                    temporary = Path(file.name)
                    json.dump({"close_behavior": behavior}, file)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            return self.get()
