import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

EXTENSIONS = {'.md', '.txt', '.html', '.htm', '.pdf', '.png', '.jpg', '.jpeg',
              '.webp', '.gif', '.svg', '.csv', '.docx', '.pptx', '.xlsx'}
MAX_FILE = 32 * 1024 * 1024


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for name in ('files', 'previews', 'staging'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.config = self.root / 'sources.json'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, relative_path TEXT NOT NULL,
                    name TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL, text TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(source, relative_path));
                CREATE TABLE IF NOT EXISTS versions (
                    item_id TEXT NOT NULL, version INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(item_id, version));
                CREATE TABLE IF NOT EXISTS import_groups (
                    name TEXT PRIMARY KEY, created_at TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(items)')}
            for column in ('deleted_at', 'deleted_by'):
                if column not in columns:
                    db.execute(f"ALTER TABLE items ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            if 'enabled' not in {r['name'] for r in db.execute('PRAGMA table_info(import_groups)')}:
                db.execute('ALTER TABLE import_groups ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'library.sqlite3', timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def sources(self):
        return json.loads(self.config.read_text(encoding='utf-8')) if self.config.exists() else []

    def register(self, name, path):
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('请填写来源名称，最多 100 个字符')
        name = name.strip()
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError('请填写本机文件夹路径')
        path = Path(path).resolve(strict=True)
        if not path.is_dir():
            raise ValueError('资料来源必须是文件夹')
        if path == Path(path.anchor) or path.is_relative_to(self.root) or self.root.is_relative_to(path):
            raise ValueError('请选择具体资料文件夹，不能登记整块磁盘或包含资料库自身的目录')
        with self.lock:
            sources = self.sources()
            existing = next((s for s in sources if s['name'] == name), None)
            with self.connect() as db:
                imported = db.execute('SELECT 1 FROM import_groups WHERE name=?', (name,)).fetchone()
            if name == '新资料' or imported:
                raise ValueError('该名称用于上传分组，请另选来源名称')
            if existing and Path(existing['path']) != path:
                raise ValueError('同名来源已经登记在另一目录')
            if existing and existing.get('enabled') is False:
                raise ValueError('该来源已移除，请在回收站选择此来源并恢复')
            if not existing:
                sources.append({'name': name, 'path': str(path)})
                temporary = self.config.with_suffix('.tmp')
                temporary.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(self.config)

    def import_group(self, folder):
        if not isinstance(folder, str) or not folder.strip() or len(folder) > 80:
            raise ValueError('文件夹名称无效')
        source = '文件夹导入 · ' + folder.strip() + ' · ' + uuid.uuid4().hex[:8]
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO import_groups(name,created_at) VALUES (?,?)', (source, datetime.now(timezone.utc).isoformat()))
        return {'source': source}

    def upload(self, value, raw):
        # Keep the group check and write together when a source is removed mid-upload.
        with self.lock:
            return self._upload(value, raw)

    def _upload(self, value, raw):
        source = value.get('source', '新资料')
        if not isinstance(source, str):
            raise ValueError('资料分组无效')
        relative = value.get('relative_path')
        if source != '新资料':
            with self.connect() as db:
                group = db.execute('SELECT enabled FROM import_groups WHERE name=?', (source,)).fetchone()
                if not group:
                    raise ValueError('请选择已建立的文件夹导入分组')
                if not group['enabled']:
                    raise ValueError('该文件夹已移除，请在回收站恢复后再导入')
            if not isinstance(relative, str) or '\\' in relative or len(relative) > 1000:
                raise ValueError('文件夹内相对路径无效')
            path = PurePosixPath(relative)
            if path.is_absolute() or any(p in {'.', '..', 'node_modules', 'logs', 'backups'}
                or p.startswith(('.', '_')) or ':' in p for p in relative.split('/')):
                raise ValueError('不导入内部目录、隐藏文件或无效路径')
            if path.name != value['name']:
                raise ValueError('文件名称与文件夹路径不匹配')
        elif relative is not None:
            raise ValueError('文件夹资料需要选择导入分组')
        return self.put(value['name'], raw, source, relative)

    def put(self, name, raw, source='新资料', relative_path=None):
        name = str(name).replace('\\', '/').split('/')[-1]
        if not name or len(name) > 200 or Path(name).suffix.lower() not in EXTENSIONS:
            raise ValueError('请选择笔记、图片、PDF、网页或办公文档')
        if len(raw) > MAX_FILE:
            raise ValueError('第一版单个文件上限为 32 MiB')
        digest = hashlib.sha256(raw).hexdigest()
        text = ''
        if Path(name).suffix.lower() in {'.md', '.txt', '.csv'} and len(raw) <= 1024 * 1024:
            text = raw.decode('utf-8', errors='replace')
        relative_path = relative_path or name
        stamp = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            old = db.execute('SELECT * FROM items WHERE source=? AND relative_path=?',
                             (source, relative_path)).fetchone()
            if old and old['deleted_at']:
                raise ValueError('这份资料在回收站中；请先恢复再更新')
            if old and old['sha256'] == digest:
                return dict(old)
            target = self.root / 'files' / digest
            if not target.exists():
                temp = self.root / 'staging' / uuid.uuid4().hex
                with temp.open('xb') as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                temp.replace(target)
            item_id = old['id'] if old else uuid.uuid4().hex
            version = old['version'] + 1 if old else 1
            db.execute('INSERT OR REPLACE INTO items(id,source,relative_path,name,version,sha256,size,text,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                       (item_id, source, relative_path, name, version, digest, len(raw), text, stamp))
            db.execute('INSERT INTO versions VALUES (?,?,?,?,?)',
                       (item_id, version, digest, len(raw), stamp))
            return dict(db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone())

    def scan(self, source_name=None):
        report = {'seen': 0, 'updated': 0, 'skipped_deleted': 0, 'errors': []}
        with self.lock:
            for source in self.sources():
                if source_name is not None and source['name'] != source_name:
                    continue
                if source.get('enabled') is False:
                    continue
                root = Path(source['path'])
                if not root.is_dir():
                    report['errors'].append(source['name'] + '：来源目录不可用，已保存版本继续保留')
                    continue
                for folder, dirs, names in os.walk(root, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))
                               and d not in {'node_modules', 'logs', 'backups'}
                               and not (Path(folder) / d).is_symlink()
                               and not getattr(Path(folder) / d, 'is_junction', lambda: False)()]
                    for name in names:
                        path = Path(folder) / name
                        if name.startswith(('.', '_')) or path.suffix.lower() not in EXTENSIONS:
                            continue
                        try:
                            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                                continue
                            if path.stat().st_size > MAX_FILE:
                                report['errors'].append(source['name'] + '/' + name + '：超过 32 MiB，未导入')
                                continue
                            relative = path.relative_to(root).as_posix()
                            with self.connect() as db:
                                old = db.execute('SELECT sha256,deleted_at FROM items WHERE source=? AND relative_path=?',
                                                 (source['name'], relative)).fetchone()
                            if old and old['deleted_at']:
                                report['skipped_deleted'] += 1
                                continue
                            result = self.put(name, path.read_bytes(), source['name'], relative)
                            report['seen'] += 1
                            report['updated'] += int(not old or old['sha256'] != result['sha256'])
                        except (OSError, ValueError) as exc:
                            report['errors'].append(source['name'] + '/' + name + '：' + (str(exc) if isinstance(exc, ValueError) else '读取失败'))
        return report

    def listing(self, query='', source='', trash=False):
        query = str(query)[:200].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with self.connect() as db:
            rows = db.execute('''SELECT id,source,relative_path,name,version,sha256,size,updated_at,deleted_at
                FROM items WHERE (name LIKE ? ESCAPE '\\' OR relative_path LIKE ? ESCAPE '\\'
                OR text LIKE ? ESCAPE '\\') AND (?='' OR source=?) AND (deleted_at!='')=?
                ORDER BY updated_at DESC, id LIMIT 500''', ('%'+query+'%',)*3 + (source, source, int(trash))).fetchall()
            count = db.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM items WHERE (deleted_at!=\'\')=?', (int(trash),)).fetchone()
            saved_sources = [r[0] for r in db.execute('SELECT DISTINCT source FROM items')]
            trash_count = db.execute("SELECT COUNT(*) FROM items WHERE deleted_at!=''").fetchone()[0]
        sources = self.sources()
        names = {s['name'] for s in sources}
        sources += [{'name': s, 'path': '', 'kind': 'import'} for s in saved_sources if s not in names]
        return {'items': [dict(r) for r in rows], 'total': count[0], 'bytes': count[1],
                'sources': sources, 'limit': 500, 'trash_count': trash_count}

    def change_item(self, item_id, restore=False):
        stamp = '' if restore else datetime.now(timezone.utc).isoformat()
        with self.lock, self.connect() as db:
            row = db.execute('SELECT deleted_at FROM items WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise KeyError('资料不存在')
            if restore or not row['deleted_at']:
                db.execute('UPDATE items SET deleted_at=?,deleted_by=? WHERE id=?',
                    (stamp, '' if restore else 'item', item_id))
        return {'ok': True, 'restored': restore}

    def change_source(self, name, restore=False):
        with self.lock:
            sources = self.sources()
            source = next((s for s in sources if s['name'] == name), None)
            with self.connect() as db:
                group = db.execute('SELECT 1 FROM import_groups WHERE name=?', (name,)).fetchone()
                if not source and not group and not db.execute('SELECT 1 FROM items WHERE source=?', (name,)).fetchone():
                    raise KeyError('来源不存在')
                if group:
                    db.execute('UPDATE import_groups SET enabled=? WHERE name=?', (int(restore), name))
                if restore:
                    changed = db.execute("UPDATE items SET deleted_at='',deleted_by='' WHERE source=? AND deleted_by=?", (name, 'source:'+name)).rowcount
                else:
                    changed = db.execute("UPDATE items SET deleted_at=?,deleted_by=? WHERE source=? AND deleted_at=''", (datetime.now(timezone.utc).isoformat(), 'source:'+name, name)).rowcount
            if source:
                source['enabled'] = restore
                temporary = self.config.with_suffix('.tmp')
                temporary.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(self.config)
        return {'ok': True, 'changed': changed, 'restored': restore}

    def get(self, item_id, version=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            if not row or row['deleted_at']:
                raise KeyError('资料不存在')
            result = dict(row)
            if version is not None:
                historical = db.execute('SELECT * FROM versions WHERE item_id=? AND version=?',
                                        (item_id, int(version))).fetchone()
                if not historical:
                    raise KeyError('资料版本不存在')
                result.update(dict(historical))
                result['text'] = ''
            result['versions'] = [dict(v) for v in db.execute(
                'SELECT version,sha256,size,created_at FROM versions WHERE item_id=? ORDER BY version DESC', (item_id,))]
        return result

    def content(self, item_id, version=None):
        item = self.get(item_id, version)
        raw = (self.root / 'files' / item['sha256']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('资料完整性校验失败')
        return item, raw
