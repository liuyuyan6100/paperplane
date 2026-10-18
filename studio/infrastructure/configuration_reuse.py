"""One-time same-machine settings import. Never read articles or copy browser profiles."""

import json
import os
import sqlite3
from pathlib import Path

from studio.config import Config
from studio.db import dumps


def stable_settings_dir():
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    return base / "PaperPlaneStudio/local"


def _receipt(data_dir):
    path = Path(data_dir) / "configuration-reuse.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _record(data_dir, value):
    path = Path(data_dir) / "configuration-reuse.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(dumps(value), encoding="utf-8")
    temporary.replace(path)
    return value


def prepare_configuration(data_dir, source_dir=None):
    data_dir = Path(data_dir).resolve()
    source = Path(source_dir or stable_settings_dir()).resolve()
    if data_dir == source or data_dir.is_relative_to(source) or source.is_relative_to(data_dir):
        raise ValueError("设置导入需要两个独立目录")
    data_dir.mkdir(parents=True, exist_ok=True)
    prior = _receipt(data_dir)
    if prior.get("complete"):
        return prior
    source_file = source / "private.json"
    if not source_file.is_file():
        return _record(data_dir, {"status": "source_not_found", "complete": False})
    if source_file.is_symlink() or (data_dir / "private.json").is_symlink():
        raise ValueError("配置文件不能是符号链接")
    target = Config(data_dir)
    original, existing = Config(source).read(), target.read()
    allowed = {
        k: v
        for k, v in original.items()
        if k.startswith(("AI_", "IMAGE_", "WECHAT_"))
        or k in ("DEVTO_API_KEY", "MODEL_SETUP_DISMISSED")
    }
    # Retired program fields do not turn an API connection into an Agent login requirement.
    missing = {k: v for k, v in allowed.items() if existing.get(k) in (None, "", [])}
    if missing:
        target.update(missing)
    accounts = source.parent / "platform-accounts.json"
    account_target = data_dir / "platform-accounts.json"
    if accounts.is_file() and not accounts.is_symlink() and not account_target.exists():
        remembered = json.loads(accounts.read_text(encoding="utf-8"))
        raw = dumps(remembered).encode("utf-8")
        fd = os.open(account_target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
    return _record(
        data_dir,
        {
            "status": "prepared",
            "complete": False,
            "private_done": True,
            "imported_fields": len(missing),
            "preserved_fields": len(allowed) - len(missing),
            "browser_session": "requires_verification",
        },
    )


def finish_configuration(studio, source_dir=None):
    data_dir = studio.db.data_dir
    prior = _receipt(data_dir)
    if prior.get("complete") or not prior.get("private_done"):
        return prior
    source = Path(source_dir or stable_settings_dir()) / "studio.sqlite3"
    copied = 0
    if source.is_file():
        from studio.platform_catalog import BY_ID

        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute("SELECT id,kind,enabled,config FROM channels").fetchall()
        with studio.db.connect(immediate=True) as db:
            for channel, kind, enabled, configuration in rows:
                if channel not in BY_ID:
                    continue
                current = db.execute(
                    "SELECT config FROM channels WHERE id=?", (channel,)
                ).fetchone()
                # Preserve an independently edited preview channel.
                if current and json.loads(current["config"]) == BY_ID[channel]["config"]:
                    db.execute(
                        "UPDATE channels SET kind=?,enabled=?,config=? WHERE id=?",
                        (kind, enabled, configuration, channel),
                    )
                    copied += 1
    return _record(data_dir, {**prior, "status": "imported", "complete": True, "channels": copied})
