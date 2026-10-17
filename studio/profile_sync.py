"""Deterministic profile replication with common baselines and optimistic writes.

Only editorial profiles and their reference images cross this boundary. Credentials,
publication jobs and running tasks are never replicated.
"""
import base64
import hashlib
import json
import re
import threading
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlsplit

from .db import dumps, now
from . import profiles

FIELDS = {**profiles.FIELDS, 'visual_style': ('视觉风格', 3000)}
MEDIA = re.compile(r'/media/[a-f0-9]{32}\.(png|jpg|webp)')


def content(value):
    from .service import Problem, text_value
    if not isinstance(value, dict):
        raise Problem('同步画像格式不正确')
    result = {k: text_value(value.get(k, ''), label, limit, k == 'name')
              for k, (label, limit) in FIELDS.items()}
    refs = value.get('visual_references', [])
    if not isinstance(refs, list) or len(refs) > 4 or any(not isinstance(r, str) or not MEDIA.fullmatch(r) for r in refs):
        raise Problem('同步参考图格式不正确')
    result['visual_references'] = list(dict.fromkeys(refs))
    # Refuse silently dropping fields introduced by a future peer.
    if set(value) - set(result) - {'id', 'version', 'created_at', 'updated_at', 'hash'}:
        raise Problem('另一端画像格式较新，请更新软件后同步', 409)
    return result


def digest(value):
    return hashlib.sha256(json.dumps(content(value), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if value else None


def records(app):
    return [{**p, 'hash': digest(p)} for p in profiles.listing(app)]


def put(app, profile_id, values, expected):
    """CAS includes absent records; retries of acknowledged content are harmless."""
    from .service import Problem
    profiles.validate_id(profile_id)
    fields = content(values)
    for ref in fields['visual_references']:
        if not (app.db.data_dir / ref.lstrip('/')).is_file():
            raise Problem('参考图尚未传输完整，请重试同步', 409)
    with app.db.connect(immediate=True) as db:
        row = db.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
        old = profiles.decode(row) if row else None
        current = digest(old)
        desired = digest(fields)
        if current == desired:
            return {**old, 'hash': current}
        if current != expected:
            raise Problem('画像在同步期间有新修改，请重新同步并核对', 409)
        if db.execute('SELECT id FROM profiles WHERE name=? AND id!=?', (fields['name'], profile_id)).fetchone():
            raise Problem('两端存在同名的不同画像，请先修改名称再同步', 409)
        stamp = now()
        result = {**fields, 'id': profile_id, 'version': old['version']+1 if old else 1,
                  'created_at': old['created_at'] if old else stamp, 'updated_at': stamp}
        if old:
            profiles.record_revision(db, old, 'baseline')
        db.execute('INSERT INTO profiles(id,name,content,version,created_at,updated_at) VALUES(?,?,?,?,?,?) '
                   'ON CONFLICT(id) DO UPDATE SET name=excluded.name,content=excluded.content,version=excluded.version,updated_at=excluded.updated_at',
                   (profile_id, fields['name'], dumps(fields), result['version'], result['created_at'], stamp))
        profiles.record_revision(db, result, 'sync')
    return {**result, 'hash': desired}


def media(app, values):
    from .service import Problem
    ref = values.get('url', '')
    if not isinstance(ref, str) or not MEDIA.fullmatch(ref):
        raise Problem('参考图路径无效')
    file = app.db.data_dir / ref.lstrip('/')
    if values['op'] == 'get_media':
        if not file.is_file():
            raise Problem('参考图不存在', 404)
        raw = file.read_bytes()
        if len(raw) > 5_000_000:
            raise Problem('参考图超过5 MB')
        return {'data': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}
    try:
        raw = base64.b64decode(values.get('data', ''), validate=True)
        if not raw or len(raw) > 5_000_000 or hashlib.sha256(raw).hexdigest() != values.get('sha256'):
            raise ValueError()
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
            if im.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError()
    except Exception:
        raise Problem('参考图内容或校验值无效') from None
    file.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a same-ID blob containing different bytes.
    try:
        with file.open('xb') as stream:
            stream.write(raw)
    except FileExistsError:
        if file.read_bytes() != raw:
            raise Problem('两端参考图编号冲突，保留原图并暂停同步', 409)
    from .assets import index_existing
    index_existing(app)
    return {'ok': True}


def exchange(app, values):
    from .service import Problem
    if values.get('protocol') != 1:
        raise Problem('同步协议不兼容', 409)
    op = values.get('op')
    if op == 'list':
        return {'protocol': 1, 'profiles': records(app)}
    if op == 'put':
        return put(app, values.get('id'), values.get('profile'), values.get('expected'))
    if op in ('get_media', 'put_media'):
        return media(app, values)
    raise Problem('不支持此同步操作')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ProfileSync:
    def __init__(self, app, transport=None):
        self.app, self.transport = app, transport
        self.lock = threading.Lock()
        self.stopped, self.wake = threading.Event(), threading.Event()
        self.worker = None
        self.state = 'idle'
        self.error = ''
        self.last_success = ''
        with app.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS profile_sync (origin TEXT NOT NULL,id TEXT NOT NULL,baseline TEXT,conflict TEXT,PRIMARY KEY(origin,id))')

    def origin(self):
        return self.app.config.get('PROFILE_SYNC_URL', '').rstrip('/')

    def status(self):
        origin = self.origin()
        conflicts = [json.loads(r['conflict']) for r in self.app.db.rows('SELECT conflict FROM profile_sync WHERE origin=? AND conflict IS NOT NULL', (origin,))]
        return {'enabled': bool(origin), 'server': origin, 'state': self.state, 'error': self.error,
                'last_success': self.last_success, 'conflicts': conflicts, 'scope': 'profiles'}

    def configure(self, values):
        with self.lock:
            return self._configure(values)

    def _configure(self, values):
        from .service import Problem
        origin = values.get('server', '')
        if not isinstance(origin, str):
            raise Problem('服务器地址无效')
        origin = origin.strip().rstrip('/')
        parsed = urlsplit(origin)
        if origin and (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise Problem('请填写 HTTPS 服务器地址，不带路径')
        old = self.origin()
        updates = {'PROFILE_SYNC_URL': origin}
        if old != origin:
            # A device credential is bound to one origin, never sent to a new host.
            updates.update(PROFILE_SYNC_CLIENT_ID='', PROFILE_SYNC_CLIENT_SECRET='')
        for key, field in [('PROFILE_SYNC_CLIENT_ID', 'client_id'), ('PROFILE_SYNC_CLIENT_SECRET', 'client_secret')]:
            if field in values:
                if not isinstance(values[field], str) or len(values[field]) > 1024:
                    raise Problem('设备凭据格式不正确')
                updates[key] = values[field]
        self.app.config.update(updates)
        self.wake.set()
        return self.status()

    def request(self, **values):
        from .service import Problem
        values['protocol'] = 1
        if self.transport:
            return self.transport(values)
        config = self.app.config
        origin = self.origin()
        # Validate every use, including configs entered outside the UI.
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise Problem('同步服务器地址无效')
        if not config.get('PROFILE_SYNC_CLIENT_ID') or not config.get('PROFILE_SYNC_CLIENT_SECRET'):
            raise Problem('请在画像同步设置中配置这台设备的连接凭据', 401)
        req = Request(origin+'/api/profile-sync', data=dumps(values).encode(), headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) PaperPlaneStudio/0.1.11',
            'Content-Type': 'application/json', 'X-PaperPlane-Device': config.get('PROFILE_SYNC_CLIENT_ID'),
            'X-PaperPlane-Sync-Key': config.get('PROFILE_SYNC_CLIENT_SECRET')})
        try:
            with build_opener(NoRedirect()).open(req, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 409:
                raise Problem('另一端画像已修改或名称、参考图冲突，请重新同步后核对', 409) from None
            if exc.code in (301, 302, 401, 403):
                raise Problem('同步设备授权已失效，请重新配置连接凭据', 401) from None
            raise Problem('同步服务器暂时不可用，请稍后重试', 503) from None
        except Exception:
            raise Problem('暂时无法连接同步服务器；本机修改已保留，联网后重试', 503) from None

    def ledger(self, profile_id, baseline=None, conflict=None):
        with self.app.db.connect() as db:
            db.execute('INSERT INTO profile_sync(origin,id,baseline,conflict) VALUES(?,?,?,?) '
                       'ON CONFLICT(origin,id) DO UPDATE SET baseline=excluded.baseline,conflict=excluded.conflict',
                       (self.origin(), profile_id, baseline, dumps(conflict) if conflict else None))

    def transfer(self, profile, upload):
        for ref in content(profile)['visual_references']:
            blob = media(self.app, {'op': 'get_media', 'url': ref}) if upload else self.request(op='get_media', url=ref)
            if upload:
                self.request(op='put_media', url=ref, **blob)
            else:
                media(self.app, {'op': 'put_media', 'url': ref, **blob})

    def run(self):
        if not self.lock.acquire(blocking=False):
            return self.status()
        try:
            if not self.origin():
                return self.status()
            self.state, self.error = 'syncing', ''
            remote = {p['id']: p for p in self.request(op='list')['profiles']}
            local = {p['id']: p for p in records(self.app)}
            baselines = {r['id']: r['baseline'] for r in self.app.db.rows('SELECT id,baseline FROM profile_sync WHERE origin=?', (self.origin(),))}
            for pid in sorted(set(remote) | set(local)):
                l, r = local.get(pid), remote.get(pid)
                lh, rh, base = digest(l), digest(r), baselines.get(pid)
                if lh == rh:
                    self.ledger(pid, lh)
                elif l and (not r or rh == base):
                    self.transfer(l, True)
                    self.request(op='put', id=pid, profile=content(l), expected=rh)
                    self.ledger(pid, lh)
                elif r and (not l or lh == base):
                    self.transfer(r, False)
                    put(self.app, pid, content(r), lh)
                    self.ledger(pid, rh)
                else:
                    self.ledger(pid, base, {'id': pid, 'local': l, 'remote': r, 'local_hash': lh, 'remote_hash': rh})
            self.last_success = now()
            self.state = 'conflict' if self.status()['conflicts'] else 'synced'
        except Exception as exc:
            from .service import Problem
            self.state = 'needs_auth' if isinstance(exc, Problem) and exc.status == 401 else 'offline'
            self.error = str(exc) if isinstance(exc, Problem) else '同步暂未完成；本机修改保留，稍后自动重试'
        finally:
            self.lock.release()
        return self.status()

    def resolve(self, values):
        from .service import Problem
        with self.lock:
            pid = profiles.validate_id(values.get('id'))
            row = self.app.db.one('SELECT * FROM profile_sync WHERE origin=? AND id=?', (self.origin(), pid))
            if not row or not row['conflict']:
                raise Problem('冲突已变更，请重新同步后核对', 409)
            old = json.loads(row['conflict'])
            if values.get('local_hash') != old['local_hash'] or values.get('remote_hash') != old['remote_hash']:
                raise Problem('页面中的冲突已过期，请重新核对', 409)
            local = next((p for p in records(self.app) if p['id'] == pid), None)
            remote = next((p for p in self.request(op='list')['profiles'] if p['id'] == pid), None)
            if digest(local) != old['local_hash'] or digest(remote) != old['remote_hash']:
                raise Problem('核对期间画像又有修改，请重新同步后选择', 409)
            choice = values.get('choice')
            selected = old.get(choice) if choice in ('local', 'remote') else values.get('profile') if choice == 'merge' else None
            chosen = content(selected)
            self.transfer(old['remote'], False)
            self.transfer(old['local'], True)
            # Save choice locally first. A remote failure keeps the conflict and choice.
            saved = put(self.app, pid, chosen, digest(local))
            self.ledger(pid, row['baseline'], {**old, 'local': saved, 'local_hash': digest(saved)})
            self.request(op='put', id=pid, profile=chosen, expected=digest(remote))
            self.ledger(pid, digest(chosen))
        self.wake.set()
        return self.status()

    def start(self):
        def loop():
            while not self.stopped.is_set():
                self.run()
                self.wake.wait(60)
                self.wake.clear()
        self.worker = threading.Thread(target=loop, name='profile-sync', daemon=True)
        self.worker.start()

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=2)
