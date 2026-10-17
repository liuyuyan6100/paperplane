"""Offline, integrity-checked restoration of a complete PaperPlane ZIP backup."""
import argparse
import hashlib
import io
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from run import InstanceLock
from studio.db import Database, event, now
from studio.migration import backup_bundle
from studio.service import Studio
from studio.paths import default_data_dir


def restore(archive_path, data_dir):
    data_dir = Path(data_dir).resolve()
    lock = InstanceLock(data_dir)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [x.filename for x in infos]
            if len(names) != len(set(names)) or len(names) > 5000 or sum(x.file_size for x in infos) > 200_000_000:
                raise ValueError("Backup is duplicated or exceeds the 200 MB / 5000 file restore limit")
            if any(not (n in ("manifest.json", "studio.sqlite3") or re.fullmatch(r"media/[a-f0-9]{32}\.(png|jpg|gif|webp)|videos/[a-f0-9]{32}\.mp4", n)) for n in names):
                raise ValueError("Backup contains an unsupported or unsafe file path")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("app") != "paperplane-studio" or manifest.get("version") != 1 or set(manifest.get("files", {})) != set(names) - {"manifest.json"}:
                raise ValueError("Unsupported backup manifest")
            for name, checksum in manifest["files"].items():
                if hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                    raise ValueError("Backup checksum mismatch")
            with tempfile.TemporaryDirectory(prefix="restore-", dir=data_dir) as folder:
                candidate = Path(folder) / "studio.sqlite3"
                candidate.write_bytes(archive.read("studio.sqlite3"))
                with closing(sqlite3.connect(candidate)) as check:
                    if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or check.execute("PRAGMA foreign_key_check").fetchall():
                        raise ValueError("Backup database integrity check failed")
                    tables = {x[0] for x in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"posts", "revisions", "jobs", "channels", "publications", "events", "settings"} <= tables or check.execute("PRAGMA user_version").fetchone()[0] != 1:
                        raise ValueError("Backup database schema is incompatible")
                    for job_id, status, channel in check.execute("SELECT id,status,channel_id FROM jobs WHERE status IN ('queued','running')").fetchall():
                        state = "uncertain" if status == "running" and channel != "local" else "canceled"
                        check.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?", (state, "从备份恢复：为避免重复发送，原排期未自动执行", now(), job_id))
                        event(check, job_id, state, "备份恢复，暂停原排期，请重新检查后安排发布")
                    if "workflows" in tables:
                        check.execute("UPDATE workflows SET status='interrupted',error=?,updated_at=? WHERE status IN ('queued','running')",
                                      ("从备份恢复：请核对原任务后手动重试，避免重复调用模型", now()))
                    if "topic_plans" in tables:
                        check.execute("UPDATE topic_plans SET status='interrupted',run_token='',error=?,updated_at=? WHERE status IN ('queued','running')",
                                      ("从备份恢复：请核对选题后手动重试", now()))
                    if "repurpose_variants" in tables:
                        check.execute("UPDATE repurpose_variants SET status='interrupted',run_token='',error=?,updated_at=? WHERE status IN ('queued','running')",
                                      ("从备份恢复：请核对平台稿后手动重试", now()))
                    if "conversation_turns" in tables:
                        check.execute("UPDATE conversation_turns SET status='interrupted',error=?,updated_at=? WHERE status IN ('queued','running')",
                                      ("从备份恢复：已保存的操作记录保留，请核对后继续；不自动重放对话", now()))
                    if "conversation_steps" in tables:
                        check.execute("UPDATE conversation_steps SET status='uncertain' WHERE status='running'")
                    for table in ('illustration_plans', 'illustration_candidates', 'editorial_reviews','draft_preparations'):
                        if table in tables:
                            check.execute(f"UPDATE {table} SET status='interrupted',run_token='',error=?,updated_at=? WHERE status IN ('queued','running')",
                                          ('从备份恢复：请核对任务后手动重试', now()))
                    if 'article_draft_intents' in tables:
                        check.execute("UPDATE article_draft_intents SET status='canceled',updated_at=? WHERE status IN ('waiting','checking')",(now(),))
                    check.commit()
                before = None
                if (data_dir / "studio.sqlite3").exists():
                    previous = Studio(data_dir)
                    bundle = backup_bundle(previous)
                    before = data_dir / "backups" / ("before-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".zip")
                    before.write_bytes(bundle)
                for name in manifest["files"]:
                    if name.startswith(("media/","videos/")):
                        destination = data_dir / name
                        destination.parent.mkdir(exist_ok=True)
                        destination.write_bytes(archive.read(name))
                # SQLite backup commits the restored DB atomically and handles existing WAL files.
                with closing(sqlite3.connect(candidate)) as source, closing(sqlite3.connect(data_dir / "studio.sqlite3")) as destination:
                    source.backup(destination)
                return {"restored": True, "data_dir": str(data_dir), "previous_backup": str(before) if before else None, "queued_jobs": "canceled; reschedule after reviewing"}
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(ROOT))
    args = parser.parse_args()
    try:
        result = restore(args.backup, args.data_dir)
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise SystemExit(f"Restore refused: {exc}") from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
