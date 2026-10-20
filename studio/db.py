import json
import sqlite3
import uuid
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
 summary TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
 tags TEXT NOT NULL DEFAULT '[]', cover TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'idea',
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0, legacy_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS preview_article_trash (
 post_id TEXT PRIMARY KEY REFERENCES posts(id),
 previous_archived INTEGER NOT NULL, trashed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
 id INTEGER PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id),
 version INTEGER NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(post_id, version)
);
CREATE TABLE IF NOT EXISTS channels (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, post_id TEXT NOT NULL REFERENCES posts(id),
 channel_id TEXT NOT NULL REFERENCES channels(id), version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, status TEXT NOT NULL, due_at TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 url TEXT NOT NULL DEFAULT '', external_id TEXT NOT NULL DEFAULT '',
 error TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 reads INTEGER NOT NULL DEFAULT 0, likes INTEGER NOT NULL DEFAULT 0,
 comments INTEGER NOT NULL DEFAULT 0, metrics_at TEXT, legacy_key TEXT UNIQUE,
 deleted_at TEXT, run_token TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_delivery ON jobs(post_id,channel_id,version) WHERE status != 'canceled';
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status,due_at);
CREATE INDEX IF NOT EXISTS idx_jobs_post ON jobs(post_id,created_at);
CREATE TABLE IF NOT EXISTS publication_checks (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 draft_id TEXT NOT NULL, draft_url TEXT NOT NULL DEFAULT '', account_id TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT '',
 checked_at TEXT, next_check_at TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
 receipt_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_publication_checks_due ON publication_checks(next_check_at);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 at TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id,id);
CREATE TABLE IF NOT EXISTS publications (
 job_id TEXT PRIMARY KEY, post_id TEXT NOT NULL,
 snapshot TEXT NOT NULL, published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_tombstones (
 job_id TEXT PRIMARY KEY, post_id TEXT NOT NULL, channel_id TEXT NOT NULL,
 version INTEGER NOT NULL, legacy_key TEXT, status TEXT NOT NULL, purged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delivery_tombstones_version ON delivery_tombstones(post_id,channel_id,version);
CREATE INDEX IF NOT EXISTS idx_delivery_tombstones_legacy ON delivery_tombstones(legacy_key);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, content TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile_revisions (
 profile_id TEXT NOT NULL REFERENCES profiles(id), version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL,
 details TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(profile_id,version)
);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 collection TEXT NOT NULL DEFAULT '', step INTEGER NOT NULL DEFAULT 0,
 description TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'upload',
 archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
PRAGMA user_version=1;
"""


def _detach_publications(db):
    """Keep reader URLs and content after the corresponding queue row is purged."""
    if not any(row["table"] == "jobs" for row in db.execute("PRAGMA foreign_key_list(publications)")):
        return
    db.execute("CREATE TABLE publications_retained (job_id TEXT PRIMARY KEY, post_id TEXT NOT NULL, snapshot TEXT NOT NULL, published_at TEXT NOT NULL)")
    db.execute("INSERT INTO publications_retained(job_id,post_id,snapshot,published_at) SELECT job_id,post_id,snapshot,published_at FROM publications")
    db.execute("DROP TABLE publications")
    db.execute("ALTER TABLE publications_retained RENAME TO publications")


class Database:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "studio.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            # Schema updates and retained-publication migration commit together.
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            if "xhs" not in {r[1] for r in db.execute("PRAGMA table_info(posts)")}:
                db.execute("ALTER TABLE posts ADD COLUMN xhs TEXT NOT NULL DEFAULT '{}'")
            if "profile_id" not in {r[1] for r in db.execute("PRAGMA table_info(posts)")}:
                db.execute("ALTER TABLE posts ADD COLUMN profile_id TEXT REFERENCES profiles(id)")
            # Old installations only have the current profile, not earlier versions.
            from .profiles import decode as decode_profile, record_revision
            for profile_row in db.execute('SELECT p.* FROM profiles p WHERE NOT EXISTS '
                                          '(SELECT 1 FROM profile_revisions r WHERE r.profile_id=p.id AND r.version=p.version)').fetchall():
                record_revision(db, decode_profile(profile_row), 'baseline')
            for name, definition in {"source_post_id": "TEXT REFERENCES posts(id)",
                                     "source_version": "INTEGER NOT NULL DEFAULT 0",
                                     "target_channel": "TEXT NOT NULL DEFAULT ''"}.items():
                if name not in {r[1] for r in db.execute("PRAGMA table_info(posts)")}:
                    db.execute(f"ALTER TABLE posts ADD COLUMN {name} {definition}")
            if "deleted_at" not in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN deleted_at TEXT")
            if "run_token" not in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN run_token TEXT NOT NULL DEFAULT ''")
            from .memories import SCHEMA as MEMORY_SCHEMA
            for statement in MEMORY_SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            from .profile_learning import SCHEMA as LEARNING_SCHEMA
            for statement in LEARNING_SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            from .model_provenance import SCHEMA as MODEL_SCHEMA
            for statement in MODEL_SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            _detach_publications(db)
            from .platform_catalog import CATALOG, RETIRED_PLATFORMS
            for retired in RETIRED_PLATFORMS:
                db.execute('UPDATE channels SET enabled=0 WHERE id=?', (retired,))
            for p in CATALOG:
                db.execute("INSERT OR IGNORE INTO channels(id,name,kind,enabled,config) VALUES(?,?,?,?,?)",
                           (p['id'],p['name'],p['kind'],int(p['default_enabled']),dumps(p['config'])))

    @contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, params=()):
        with self.connect() as db:
            return [dict(x) for x in db.execute(sql, params)]

    def one(self, sql, params=()):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def backup(self):
        folder = self.data_dir / "backups"
        folder.mkdir(exist_ok=True)
        name = datetime.now().strftime("studio-%Y%m%d-%H%M%S-") + uid()[:6] + ".sqlite3"
        target = folder / name
        with self.connect() as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        return target


def decode_post(row):
    value = dict(row)
    value["tags"] = json.loads(value["tags"])
    value["xhs"] = json.loads(value.get("xhs", "{}"))
    return value


def event(db, job_id, status, message):
    db.execute("INSERT INTO events(job_id,at,status,message) VALUES(?,?,?,?)", (job_id, now(), status, message))
