"""Derive channel progress from evidence; registering history never dispatches delivery."""
import json
from .db import decode_post, dumps, event, now, uid


def annotate(app, posts, jobs):
    variants = app.db.rows('SELECT source_post_id,platform,status FROM repurpose_variants')
    for post in posts:
        family = {post['id']}
        if not post.get('source_post_id'):
            family.update(p['id'] for p in posts if p.get('source_post_id') == post['id'])
        related = [j for j in jobs if j['post_id'] in family and not j.get('deleted_at')]
        published = {j['channel_id'] for j in related if j['status'] == 'published'}
        pending = {j['channel_id'] for j in related if j['status'] not in ('published', 'canceled')}
        pending.update(v['platform'] for v in variants if v['source_post_id'] == post['id'])
        pending.update(p['target_channel'] for p in posts if p.get('source_post_id') == post['id'] and not p['archived'] and p.get('target_channel'))
        pending -= published
        post['publication_progress'] = {'published': sorted(published), 'pending': sorted(pending),
            'complete': bool(published) and not pending}
    return posts


def register(app, post_id, values):
    from .service import Problem, text_value, public_article_url
    channels = values.get('channels')
    if not isinstance(channels, list) or not channels or len(channels) > len(app.channels()) or any(not isinstance(c, str) for c in channels) or len(set(channels)) != len(channels):
        raise Problem('请选择已公开发布的平台')
    if values.get('confirmed') is not True:
        raise Problem('请明确确认所选平台已公开发布')
    note = text_value(values.get('note', ''), '备注', 1500)
    url = text_value(values.get('url', ''), '公开链接', 4000)
    if url and (len(channels) != 1 or not public_article_url({'channel_id': channels[0], 'url': url})):
        raise Problem('请填写所选平台的公开文章链接，不能使用编辑页或草稿地址')
    with app.db.connect(immediate=True) as db:
        row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
        if not row: raise Problem('文章不存在', 404)
        if type(values.get('version')) is not int or values['version'] != row['version']:
            raise Problem('稿件已变化，请重新核对后登记', 409)
        known = {r['id'] for r in db.execute('SELECT id FROM channels')}
        if any(c not in known or c == 'local' for c in channels):
            raise Problem('请选择受支持的外部平台')
        stamp = now()
        for channel in channels:
            if db.execute("SELECT 1 FROM jobs WHERE post_id=? AND channel_id=? AND deleted_at IS NULL AND status IN ('queued','running')", (post_id,channel)).fetchone():
                raise Problem('该平台仍有排队或运行中的任务，请先核对并停止待执行任务，避免重复发送',409)
            if db.execute("SELECT 1 FROM jobs WHERE post_id=? AND channel_id=? AND status='published' AND deleted_at IS NULL",(post_id,channel)).fetchone():
                continue
            existing = db.execute("SELECT * FROM jobs WHERE post_id=? AND channel_id=? AND version=? AND status!='canceled'",(post_id,channel,row['version'])).fetchone()
            if existing and existing['deleted_at']:
                raise Problem('已有记录在回收站，请先恢复后确认',409)
            message = '用户确认：已在平台公开发布。登记时间不代表实际发表时间；稿件快照仅供本地参考。' + note
            if existing:
                ident = existing['id']
                db.execute("UPDATE jobs SET status='published',error='',note=?,updated_at=? WHERE id=?",(message,stamp,ident))
            else:
                ident = uid()
                db.execute("INSERT INTO jobs(id,run_id,post_id,channel_id,version,snapshot,status,due_at,created_at,updated_at,note) VALUES(?,?,?,?,?,?,'published',?,?,?,?)",
                    (ident,uid(),post_id,channel,row['version'],dumps(decode_post(row)),stamp,stamp,stamp,message))
            event(db,ident,'published',message)
            if url:
                db.execute('UPDATE jobs SET url=? WHERE id=?', (url, ident))
    return {'ok': True}
