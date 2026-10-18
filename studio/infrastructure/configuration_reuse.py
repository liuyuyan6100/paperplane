"""One-time same-machine settings import. Never read articles or copy browser profiles."""

import json
import os
import re
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
    if prior.get("complete"):
        return _finish_profiles(studio, prior, source_dir)
    if not prior.get("private_done"):
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
    return _finish_profiles(
        studio, {**prior, "status": "imported", "complete": True, "channels": copied}, source_dir
    )


def _finish_profiles(studio, receipt, source_dir):
    """M2.2 adds saved profile definitions/styles, including existing M2.1 imports."""
    if receipt.get("profiles_done"):
        return receipt
    source = Path(source_dir or stable_settings_dir()).resolve()
    target = studio.db.data_dir.resolve()
    if source == target or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("画像导入需要两个独立目录")
    database = source / "studio.sqlite3"
    imported = preserved = missing = 0
    if database.is_file():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM profiles").fetchall()
        from studio.profiles import decode, record_revision

        with studio.db.connect(immediate=True) as db:
            for row in rows:
                if db.execute(
                    "SELECT 1 FROM profiles WHERE id=? OR name=?", (row["id"], row["name"])
                ).fetchone():
                    preserved += 1
                    continue
                content = json.loads(row["content"])
                references = content.get("visual_references", [])
                ready = True
                for url in references:
                    if not isinstance(url, str) or not re.fullmatch(
                        r"/media/[a-f0-9]{32}\.(png|jpg|webp)", url
                    ):
                        ready = False
                        break
                    original, destination = (
                        source / "media" / Path(url).name,
                        target / "media" / Path(url).name,
                    )
                    if (
                        not original.is_file()
                        or original.is_symlink()
                        or original.stat().st_size > 20_000_000
                    ):
                        ready = False
                        break
                    if not original.resolve().is_relative_to(
                        source
                    ) or not destination.resolve().is_relative_to(target):
                        ready = False
                        break
                    raw = original.read_bytes()
                    if destination.exists():
                        if destination.is_symlink() or destination.read_bytes() != raw:
                            ready = False
                            break
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with destination.open("xb") as stream:
                            stream.write(raw)
                if not ready:
                    missing += 1
                    continue
                db.execute(
                    "INSERT INTO profiles(id,name,content,version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    tuple(
                        row[k]
                        for k in ("id", "name", "content", "version", "created_at", "updated_at")
                    ),
                )
                record_revision(db, decode(row), "import", {"source": "same_machine_settings"})
                imported += 1
    return _record(
        target,
        {
            **receipt,
            "profiles_done": missing == 0,
            "profiles_imported": imported,
            "profiles_preserved": preserved,
            "profiles_missing_references": missing,
        },
    )
