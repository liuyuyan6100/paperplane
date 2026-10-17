"""Read an existing private draft without repeating a delivery request."""
import json
import re

from .adapters import DeliveryError
from .db import dumps, event, now
from . import platform_catalog

SUPPORTED = {'cnblogs', 'oschina', 'segmentfault', '51cto', 'toutiao', 'bilibili-article', 'zhihu'}


def reconcile(app, job_id, values):
    from .service import Problem
    bridge = app.publishing_bridge
    job = app.db.one('SELECT * FROM jobs WHERE id=?', (job_id,))
    if not job:
        raise Problem('任务不存在', 404)
    job = dict(job)
    snapshot = json.loads(job['snapshot'])
    if (job['deleted_at'] is not None or job['status'] not in ('uncertain', 'draft')
            or job['channel_id'] not in SUPPORTED
            or snapshot.get('delivery_config', {}).get('mode') != 'desktop-publishing'):
        raise Problem('此任务不支持只读核对草稿', 409)
    draft_id = str(values.get('draft_id') or job['external_id'] or '')
    if not re.fullmatch(r'[1-9][0-9]{0,39}', draft_id):
        raise Problem('请填写平台后台中这篇文章的草稿编号；核对不会新建草稿')
    if job['external_id'] and draft_id != job['external_id']:
        raise Problem('请核对原任务的草稿，不能替换已有草稿编号', 409)
    expected = snapshot.get('desktop_submission')
    if not expected:
        raise Problem('旧任务没有保留上传后的正文快照，请先在平台打开原草稿人工核对；不会重发', 409)
    if not bridge.browser or bridge.busy or not bridge.serial.acquire(blocking=False):
        raise Problem('平台窗口不可用或正在处理其他任务，请稍后核对', 409)
    bridge.busy = True
    try:
        result = bridge.browser.read_draft(job['channel_id'], draft_id, expected)
        from .publishing_bridge import valid_url
        if (not isinstance(result, dict) or result.get('incomplete') is True
                or str(result.get('id', '')) != draft_id
                or not valid_url(result.get('url'), job['channel_id'])):
            reason = str(result.get('reason', ''))[:160] if isinstance(result, dict) else ''
            message = '草稿内容或账号未能完整核对，保留原状态；不会重发。' + reason
            if isinstance(result, dict) and isinstance(result.get('observed'), dict):
                raise Problem(message, 502, {'draft_readback': result['observed']})
            raise DeliveryError(message)
        urls = {
            'cnblogs': f'https://i.cnblogs.com/articles/edit;postId={draft_id}',
            'oschina': f"https://my.oschina.net/u/{expected['account_id']}/blog/ai-write/draft/{draft_id}",
            'segmentfault': f'https://segmentfault.com/write?draftId={draft_id}',
            '51cto': f'https://blog.51cto.com/blogger/draft/{draft_id}',
            'toutiao': f'https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id={draft_id}',
            'bilibili-article': f'https://member.bilibili.com/platform/upload/text/edit?aid={draft_id}',
            'zhihu': f'https://zhuanlan.zhihu.com/p/{draft_id}/edit',
        }
        if result['url'] != urls[job['channel_id']]:
            raise DeliveryError('草稿地址与原账号或编号不一致，保留原任务状态。')
        fingerprint = bridge.fingerprint(job['channel_id'])
        with app.db.connect(immediate=True) as db:
            fresh = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            keys = ('status', 'snapshot', 'external_id', 'url', 'updated_at', 'deleted_at', 'attempts')
            if not fresh or any(fresh[k] != job[k] for k in keys):
                raise Problem('任务在核对期间已变化，请刷新后重新核对', 409)
            snapshot['desktop_receipt'] = {'draft_id': draft_id, 'draft_url': result['url'],
                'account_id': expected['account_id'], 'verified_at': now()}
            note = '已回读并核实原草稿的账号、标题、正文和配图；尚未公开发表。'
            db.execute("UPDATE jobs SET status='draft',external_id=?,url=?,snapshot=?,note=?,error='',updated_at=? WHERE id=?",
                       (draft_id, result['url'], dumps(snapshot), note, now(), job_id))
            event(db, job_id, 'draft', note)
            platform_catalog.record_success(app, db, job['channel_id'],
                {'status': 'draft', 'external_id': draft_id}, fingerprint, job_id)
        return {'ok': True, 'job_id': job_id, 'status': 'draft', 'url': result['url']}
    finally:
        bridge.busy = False
        bridge.serial.release()
