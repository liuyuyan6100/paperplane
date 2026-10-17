"""Draft-only Xiaohongshu delivery through the desktop's persistent browser.

No cookies, private platform API or public-publish operation are exposed here.
The browser may auto-save as soon as images are added: subsequent failures are
uncertain, never safe to automatically retry.
"""
import hashlib
import json
import re
import threading
from pathlib import Path

from .adapters import DeliveryError
from .db import dumps, event, now, uid
from .title_rules import adapt_xhs_title, xhs_title_valid


MODE = 'xhs-browser'


def valid_account(account):
    return (isinstance(account, dict) and isinstance(account.get('id'), str)
            and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', account['id']) is not None
            and isinstance(account.get('name'), str))


def validate_note(snapshot, data_dir):
    note = snapshot.get('xhs_delivery') or snapshot.get('xhs', {})
    title, caption, photos = (note.get(k) for k in ('title', 'caption', 'photos'))
    if not isinstance(title, str) or not title.strip() or len(title) > 20:
        raise DeliveryError('小红书标题请控制在 20 字以内；请修改标题后再送草稿，不会自动截断。')
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 1000:
        raise DeliveryError('小红书文案需为 1–1000 字，请修改后再送草稿。')
    if (not isinstance(photos, list) or not 1 <= len(photos) <= 18
            or any(not isinstance(p, str) for p in photos) or len(set(photos)) != len(photos)):
        raise DeliveryError('小红书图集需有 1–18 张不同的图片。')
    root = (Path(data_dir) / 'media').resolve()
    paths = []
    for src in photos:
        if not isinstance(src, str) or not re.fullmatch(r'/media/[a-f0-9]{32}\.(png|jpg|webp)', src):
            raise DeliveryError('自动送草稿只支持素材库中的 PNG、JPG、WebP 图片。')
        path = (root / Path(src).name).resolve()
        if path.parent != root or not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
            raise DeliveryError('小红书原图缺失、为空或超过 32 MB，请检查图片。')
        paths.append(str(path))
    return {'title': title, 'caption': caption, 'photos': photos}, paths


class XhsDrafts:
    def __init__(self, app):
        self.app = app
        self.browser = None  # Desktop injects a transport; headless/cloud cannot send.
        self.lock = threading.Lock()
        self.account = None
        self.account_state = 'unknown'
        self.progress = ''
        self.error = ''
        self.busy = False
        self.thread = None
        row = app.db.one("SELECT value FROM settings WHERE key='xhs-browser-profile'")
        self.profile = json.loads(row['value']) if row else uid()
        if not row:
            with app.db.connect() as db:
                db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', ('xhs-browser-profile', dumps(self.profile)))

    def status(self):
        return {'available': self.browser is not None, 'logged_in': valid_account(self.account) and self.account_state == 'verified',
                'account_state': self.account_state, 'account': self.account, 'busy': self.busy, 'progress': self.progress,
                'error': self.error, 'mode': 'draft', 'storage': 'desktop-browser',
                'diagnostics': dict(self.browser.diagnostics) if isinstance(getattr(self.browser, 'diagnostics', None), dict) else {}}

    def fingerprint(self):
        return hashlib.sha256(dumps([self.profile, (self.account or {}).get('id')]).encode()).hexdigest()

    def require_browser(self):
        if self.browser is None:
            raise DeliveryError('请在 Windows 安装版的本机模式使用小红书自动送草稿。')
        return self.browser

    def set_progress(self, message):
        self.progress = message

    def launch(self, job_id=None):
        """Return immediately; browser login/opening must not block an HTTP request."""
        from .service import Problem
        browser = self.require_browser()
        if not self.lock.acquire(blocking=False):
            raise Problem('小红书窗口正在处理任务，请等待当前步骤完成。', 409)
        job = None
        try:
            if job_id:
                job = self.app.job_detail(job_id)
                if job['channel_id'] != 'xiaohongshu' or job['deleted_at'] or job['status'] not in ('draft', 'uncertain'):
                    raise Problem('此记录没有可打开的小红书草稿。', 409)
            self.busy = True
            self.error = ''
            self.account_state = 'unverified'
        except Exception:
            self.lock.release()
            raise

        def run():
            try:
                receipt = (job or {}).get('snapshot', {}).get('xhs_receipt', {})
                if receipt.get('profile') and receipt['profile'] != self.profile:
                    raise DeliveryError('这份草稿属于另一个纸飞机浏览器，请在原设备打开。')
                account = browser.open_draft(receipt, self.set_progress) if job else browser.login(self.set_progress)
                if not valid_account(account):
                    raise DeliveryError('小红书账号信息未能核实，请检查窗口后重新核验。')
                self.account = account
                # open_draft may reuse an already-open editor and return the old
                # receipt account. That receipt alone is not fresh login evidence.
                if job:
                    try:
                        current = browser.verify_identity()
                    except DeliveryError:
                        current = 'unverified'
                    if valid_account(current):
                        self.account, self.account_state = current, 'verified'
                    elif current is None:
                        self.account, self.account_state = None, 'signed-out'
                else:
                    self.account_state = 'verified'
                self.progress = '已打开小红书草稿，请检查并确认发布。' if job else '小红书已登录，可自动送入草稿。'
            except Exception as exc:
                self.account_state = 'unverified'
                self.error = str(exc) if isinstance(exc, DeliveryError) else '小红书窗口未能完成操作，请检查窗口后重试。'
            finally:
                if valid_account(getattr(browser, 'account', None)):
                    self.account = browser.account
                self.busy = False
                self.lock.release()
        self.thread = threading.Thread(target=run, daemon=True, name='xhs-open')
        self.thread.start()
        return self.status()

    def queue_existing(self, job_id, values):
        """Explicitly upgrade an existing manual task, preserving its snapshot."""
        from .service import Problem
        self.require_browser()
        with self.app.db.connect(immediate=True) as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job:
                raise Problem('任务不存在', 404)
            snapshot = json.loads(job['snapshot'])
            if job['channel_id'] != 'xiaohongshu' or job['deleted_at'] or job['status'] != 'manual':
                raise Problem('只有待手动发表的小红书任务可以转为自动送草稿。', 409)
            channel = db.execute("SELECT enabled FROM channels WHERE id='xiaohongshu'").fetchone()
            if not channel['enabled']:
                raise Problem('请先启用小红书渠道。', 409)
            title = values.get('title', snapshot['xhs']['title'])
            if not isinstance(title, str) or len(title) > 2000:
                raise Problem('投递标题格式不正确或过长')
            snapshot['xhs_delivery'] = {**snapshot['xhs'], 'title': title}
            # Validate images/caption now; the worker adapts legacy titles before upload.
            validate_note({**snapshot, 'xhs_delivery': {**snapshot['xhs_delivery'], 'title': '待适配标题'}}, self.app.db.data_dir)
            snapshot['delivery_config'] = {**snapshot.get('delivery_config', {}), 'mode': MODE, 'publish': False}
            if self.account:
                snapshot['delivery_config']['account_id'] = self.account['id']
            db.execute("UPDATE jobs SET snapshot=?,status='queued',error='',note=?,due_at=?,updated_at=? WHERE id=?", (
                dumps(snapshot), '等待自动送入小红书草稿箱', now(), now(), job_id))
            event(db, job_id, 'queued', '用户选择自动送入小红书草稿；公开发表仍由用户确认。')
        self.app.wake.set()
        return {'ok': True}

    def send(self, snapshot, job_id):
        browser = self.require_browser()
        if not self.lock.acquire(blocking=False):
            raise DeliveryError('小红书窗口正在处理另一项操作，请完成后重试。')
        self.busy = True
        self.error = ''
        try:
            if isinstance(getattr(browser, 'diagnostics', None), dict):
                browser.diagnostics = {}
            source = snapshot.get('xhs_delivery') or snapshot.get('xhs', {})
            validate_note({**snapshot, 'xhs_delivery': {**source, 'title': '待适配标题'}}, self.app.db.data_dir)
            if not xhs_title_valid(source.get('title')) or source['title'] != source['title'].strip():
                from .model_routing import route_for
                self.set_progress('正在按小红书规则自动适配标题…')
                route = route_for(self.app.config, 'repurpose')
                title = adapt_xhs_title(self.app, {**snapshot, '_model_task': 'repurpose', '_model_route': route}, source)
                snapshot['xhs_delivery'] = {**source, 'title': title}
                snapshot['xhs_title_adaptation'] = {'original': source.get('title'), 'title': title, 'model_route': route}
                # Persist before touching the platform so a retry reuses this title.
                with self.app.db.connect(immediate=True) as db:
                    cursor = db.execute("UPDATE jobs SET snapshot=?,updated_at=? WHERE id=? AND status='running' AND deleted_at IS NULL", (dumps(snapshot), now(), job_id))
                    if cursor.rowcount != 1:
                        raise DeliveryError('小红书任务已停止或状态变化，正文尚未发送。')
                    event(db, job_id, 'running', '已按小红书规则自动适配投递标题：' + title)
            note, paths = validate_note(snapshot, self.app.db.data_dir)
            # A previous successful login must not survive a failed new identity
            # attempt as if it were current evidence. Keep its account as a hint.
            self.account_state = 'unverified'
            receipt = browser.save_draft(note, paths, snapshot.get('delivery_config', {}).get('account_id'), self.set_progress)
            if (not isinstance(receipt, dict) or not re.fullmatch(r'[a-zA-Z0-9-]{8,80}', receipt.get('draft_id', ''))
                    or not valid_account(receipt.get('account')) or receipt.get('verified') is not True):
                raise DeliveryError('小红书未返回可核对的草稿，请检查草稿箱，不会自动重发。', uncertain=True)
            self.account = receipt['account']
            self.account_state = 'verified'
            receipt.update(profile=self.profile, title=note['title'], photo_count=len(paths))
            self.progress = '草稿已保存并重新打开核对，等待你确认发布。'
            return {'status': 'draft', 'external_id': receipt['draft_id'], 'url': '',
                    'note': '已保存到纸飞机内置小红书窗口的草稿箱；点击“打开小红书草稿”检查并确认发布。',
                    'xhs_receipt': receipt, 'fingerprint': self.fingerprint()}
        except DeliveryError as exc:
            self.error = str(exc)
            raise
        finally:
            try:
                # These diagnostics are best-effort evidence only. A failure here
                # must neither replace the remote receipt nor leave the lock held.
                diagnostics = getattr(browser, 'diagnostics', None)
                account = getattr(browser, 'account', None)
                if valid_account(account):
                    self.account = account
                if isinstance(diagnostics, dict) and isinstance(diagnostics.get('account_id'), str):
                    if valid_account(account) and account['id'] == diagnostics['account_id']:
                        self.account_state = 'verified'
                    evidence = {'profile': self.profile, 'captured_at': now(),
                                'diagnostics': json.loads(dumps(diagnostics))}
                    with self.app.db.connect(immediate=True) as db:
                        row = db.execute("SELECT snapshot FROM jobs WHERE id=? AND status='running' AND deleted_at IS NULL", (job_id,)).fetchone()
                        if row:
                            current = json.loads(row['snapshot'])
                            current['xhs_attempt'] = evidence
                            db.execute("UPDATE jobs SET snapshot=? WHERE id=? AND status='running' AND deleted_at IS NULL", (dumps(current), job_id))
                            snapshot['xhs_attempt'] = evidence
            except Exception:
                pass  # Diagnostics cannot change a known remote result or authorize retry.
            finally:
                self.busy = False
                self.lock.release()

    def close(self):
        if self.browser:
            self.browser.stop()
