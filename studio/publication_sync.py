"""Read-only platform publication reconciliation. Never send or publish content."""
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .adapters import DeliveryError
from .db import dumps, event, now


def later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def snapshot_of(job):
    value = job.get('snapshot', {})
    if isinstance(value, str):
        try: value = json.loads(value)
        except (ValueError, TypeError): return {}
    return value if isinstance(value, dict) else {}


def eligible(job):
    return (job.get('deleted_at') is None and job.get('status') in ('draft', 'uncertain')
            and job.get('channel_id') in ('juejin', 'csdn', 'zhihu')
            and re.fullmatch(r'[0-9]{1,40}', str(job.get('external_id', ''))) is not None
            and snapshot_of(job).get('delivery_config', {}).get('mode') in ('desktop-publishing', 'bridge'))


class PublicationSync:
    interval = 30

    def __init__(self, app):
        self.app = app
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.poll_lock = threading.Lock()
        self.checking = False

    def decorate(self, jobs):
        checks = {row['job_id']: row for row in self.app.db.rows('SELECT * FROM publication_checks')}
        for job in jobs:
            check = checks.get(job['id'])
            available = eligible(job)
            if check:
                check = dict(check)
                check['receipt'] = json.loads(check.pop('receipt_json'))
                check.update(supported=True, can_check=available)
                job['publication_sync'] = check
            else:
                job['publication_sync'] = {'supported': True, 'state': 'pending',
                    'message': '等待自动核对平台发表结果', 'checked_at': None,
                    'next_check_at': None, 'failures': 0, 'receipt': {}, 'can_check': available} if available else None
        return jobs

    def _discover(self, db):
        for row in db.execute("SELECT * FROM jobs WHERE channel_id IN ('juejin','csdn','zhihu') AND status IN ('draft','uncertain') AND deleted_at IS NULL AND external_id!=''").fetchall():
            job = dict(row)
            if not eligible(job): continue
            snapshot = snapshot_of(job)
            account = str(snapshot.get('desktop_receipt', {}).get('account_id')
                          or snapshot.get('delivery_config', {}).get('account_id') or '')
            db.execute("""INSERT OR IGNORE INTO publication_checks
                (job_id,draft_id,draft_url,account_id,state,message,next_check_at)
                VALUES(?,?,?,?,'pending','等待自动核对平台发表结果',?)""",
                (job['id'], job['external_id'], job['url'], account, now()))

    def request(self, job_id):
        from .service import Problem
        with self.app.db.connect(immediate=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row: raise Problem('任务不存在', 404)
            if not eligible(dict(row)):
                raise Problem('仅能核对已有平台草稿编号且尚未确认发表的掘金、CSDN 或知乎任务', 409)
            self._discover(db)
            check = dict(db.execute('SELECT * FROM publication_checks WHERE job_id=?', (job_id,)).fetchone())
            # Explicit refresh bypasses long backoff, but retains the 30-second request limit.
            earliest = now()
            if check['checked_at']:
                earliest = max(earliest, (datetime.fromisoformat(check['checked_at']) + timedelta(seconds=self.interval)).isoformat(timespec='seconds'))
            if check['state'] != 'checking':
                db.execute("UPDATE publication_checks SET state='pending',message='已安排核对平台发表结果',next_check_at=? WHERE job_id=?", (earliest, job_id))
        self.wake.set()
        return {'ok': True, 'queued': True, 'job_id': job_id}

    def start(self):
        if self.thread and self.thread.is_alive(): return
        with self.app.db.connect(immediate=True) as db:
            self._discover(db)
            db.execute("UPDATE publication_checks SET state='pending',message='已恢复平台发表结果核对',next_check_at=? WHERE state='checking'", (now(),))
        def loop():
            while not self.stop.is_set():
                try: self.tick()
                except Exception: pass  # Storage failures never block the delivery worker.
                self.wake.wait(2)
                self.wake.clear()
        self.thread = threading.Thread(target=loop, name='publication-reconciliation', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread: self.thread.join(timeout=3)

    @staticmethod
    def _validate(result, job, check):
        if not isinstance(result, dict) or result.get('state') not in ('published', 'reviewing', 'draft', 'unknown'):
            raise DeliveryError('平台核对没有返回有效状态，保留原发布记录。')
        state = result['state']
        draft_id, account = str(result.get('draft_id') or ''), str(result.get('account_id') or '')
        if state != 'unknown' and (draft_id != check['draft_id'] or not account or len(account) > 200):
            raise DeliveryError('平台草稿或账号身份未能核实，保留原发布记录。')
        if check['account_id'] and account and account != check['account_id']:
            raise DeliveryError('平台账号与原草稿账号不一致，请切回原账号后核对。')
        receipt = {}
        if state == 'published':
            article_id, url = str(result.get('article_id') or ''), result.get('url')
            if not re.fullmatch(r'[0-9]{1,40}', article_id) or not isinstance(url, str) or len(url) > 4000:
                raise DeliveryError('平台未提供可核实的公开文章回执。')
            valid = False
            try:
                parts = urlsplit(url)
                valid = (parts.scheme == 'https' and not parts.username and not parts.password
                         and not parts.port and not parts.query and not parts.fragment)
                if job['channel_id'] == 'juejin':
                    valid = valid and parts.hostname == 'juejin.cn' and parts.path == '/post/' + article_id
                elif job['channel_id'] == 'zhihu':
                    valid = valid and article_id == draft_id and parts.hostname == 'zhuanlan.zhihu.com' and parts.path == '/p/' + article_id
                else:
                    valid = valid and parts.hostname == 'blog.csdn.net' and parts.path == '/' + account + '/article/details/' + article_id
            except ValueError: pass
            if not valid: raise DeliveryError('公开链接与平台文章编号或账号不一致，保留原发布记录。')
            receipt = {'source': 'platform', 'draft_id': draft_id, 'draft_url': check['draft_url'],
                       'article_id': article_id, 'url': url, 'account_id': account, 'verified_at': now()}
        return state, account if state != 'unknown' else '', receipt

    def tick(self):
        if self.stop.is_set() or not self.poll_lock.acquire(blocking=False): return False
        try: return self._tick()
        finally: self.poll_lock.release()

    def _tick(self):
        bridge = self.app.publishing_bridge
        if not bridge.browser: return False
        with self.app.db.connect(immediate=True) as db:
            self._discover(db)
            # Ready deliveries take priority over routine read-back.
            if db.execute("""SELECT 1 FROM jobs WHERE channel_id IN ('juejin','csdn','zhihu')
                    AND deleted_at IS NULL AND (status='running' OR (status='queued' AND due_at<=?))
                    LIMIT 1""", (now(),)).fetchone():
                return False
            rows = db.execute("""SELECT j.* FROM jobs j JOIN publication_checks p ON p.job_id=j.id
                WHERE j.deleted_at IS NULL AND j.status IN ('draft','uncertain') AND p.next_check_at<=?
                ORDER BY p.next_check_at,j.created_at""", (now(),)).fetchall()
            job = next((dict(row) for row in rows if eligible(dict(row))), None)
            if not job: return False
            check = dict(db.execute('SELECT * FROM publication_checks WHERE job_id=?', (job['id'],)).fetchone())
            last_event = db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE job_id=?', (job['id'],)).fetchone()[0]
        if not bridge.serial.acquire(blocking=False): return False
        self.checking = True
        try:
            with self.app.db.connect(immediate=True) as db:
                db.execute("UPDATE publication_checks SET state='checking',message='正在核对平台发表结果',next_check_at=? WHERE job_id=?", (later(self.interval), job['id']))
            try:
                if check['draft_id'] != job['external_id']:
                    raise DeliveryError('原草稿编号已变化，请核对任务记录。')
                result = bridge.browser.check_publication(job['channel_id'], check['draft_id'], check['account_id'] or None)
                state, account, receipt = self._validate(result, job, check)
                message = {'published': '已自动核实公开发表并同步文章链接', 'reviewing': '平台正在审核，尚未确认公开发表',
                           'draft': '平台仍为草稿，尚未公开发表', 'unknown': '暂时无法确认发表结果，保留原发布记录'}[state]
                if state == 'unknown' and isinstance(result.get('message'), str): message = result['message'][:300]
                failures = check['failures'] + 1 if state == 'unknown' else 0
            except Exception as exc:
                state, account, receipt = 'unknown', '', {}
                message = str(exc)[:300] if isinstance(exc, DeliveryError) else '平台发表结果核对失败，稍后自动重试；原记录保留。'
                failures = check['failures'] + 1
            delay = min(1800, 30 * (2 ** min(failures, 6))) if failures else self.interval
            with self.app.db.connect(immediate=True) as db:
                fresh = db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
                if not fresh: return True
                # Never overwrite a concurrent user confirmation, deletion, retry, or new receipt.
                keys = ('post_id', 'channel_id', 'version', 'snapshot', 'status', 'external_id', 'url', 'deleted_at', 'updated_at')
                fresh_event = db.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE job_id=?', (job['id'],)).fetchone()[0]
                if self.stop.is_set() or fresh_event != last_event or any(fresh[k] != job[k] for k in keys):
                    db.execute("UPDATE publication_checks SET state='unknown',message='任务状态已变化，保留最新记录',checked_at=?,next_check_at=? WHERE job_id=?", (now(), later(self.interval), job['id']))
                    return True
                db.execute("""UPDATE publication_checks SET state=?,message=?,checked_at=?,next_check_at=?,failures=?,
                    account_id=CASE WHEN account_id='' AND ?!='' THEN ? ELSE account_id END,
                    receipt_json=CASE WHEN ?!='{}' THEN ? ELSE receipt_json END WHERE job_id=?""",
                    (state, message, now(), later(delay), failures, account, account, dumps(receipt), dumps(receipt), job['id']))
                if state == 'published':
                    db.execute("UPDATE jobs SET status='published',url=?,note=?,error='',updated_at=? WHERE id=?", (receipt['url'], message, now(), job['id']))
                    event(db, job['id'], 'published', message + '；原草稿：' + check['draft_url'])
                elif state != check['state'] or message != check['message']:
                    event(db, job['id'], job['status'], '发表核对：' + message)
            return True
        finally:
            self.checking = False
            bridge.serial.release()
