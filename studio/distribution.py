"""Persist the user's review gate and dispatch only real public-publishing adapters."""
from .db import now

SCHEMA='''CREATE TABLE IF NOT EXISTS distribution_intents (
 variant_id TEXT PRIMARY KEY REFERENCES repurpose_variants(id),
 post_id TEXT NOT NULL REFERENCES posts(id), post_version INTEGER NOT NULL,
 channel_id TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
 job_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);'''


def get(app, variant_id):
    row=app.db.one('SELECT * FROM distribution_intents WHERE variant_id=?',(variant_id,))
    if row and row['job_id']:
        job=app.db.one('SELECT status,error,note,url FROM jobs WHERE id=?',(row['job_id'],))
        if job:row.update(status=job['status'],message=job['error'] or job['note'],url=job['url'])
    return row


def dispatch(app, variant_id):
    from .service import Problem
    intent=get(app,variant_id)
    if not intent or intent['job_id']:return intent
    post=app.post(intent['post_id']);channel=intent['channel_id']
    mother=post.get('source_post_id') or post['id']
    published=app.db.one("SELECT j.id FROM jobs j JOIN posts p ON p.id=j.post_id WHERE (p.id=? OR p.source_post_id=?) AND j.channel_id=? AND j.status='published' AND j.deleted_at IS NULL LIMIT 1",(mother,mother,channel))
    if published:
        with app.db.connect(immediate=True) as db:db.execute("UPDATE distribution_intents SET status='published',message='该平台已有公开发布记录，已跳过重复发表。',job_id=?,updated_at=? WHERE variant_id=?",(published['id'],now(),variant_id))
        return get(app,variant_id)
    entry=next((p for p in app.channels() if p['id']==channel),None)
    message=''
    if post['version']!=intent['post_version'] or post['archived']:
        message='采用后稿件已修改或归档，请重新审阅当前稿件后从发布检查发起。'
    elif not entry or not entry['enabled'] or not entry['delivery_implemented']:
        message='图文已采用；'+(entry['name'] if entry else channel)+'自动发送连接尚未接通或平台未启用，未发送。'
    else:
        existing=app.db.one("SELECT id,status FROM jobs WHERE post_id=? AND channel_id=? AND version=? AND status!='canceled'",(post['id'],channel,post['version']))
        if existing:
            with app.db.connect(immediate=True) as db:db.execute('UPDATE distribution_intents SET job_id=?,status=?,updated_at=? WHERE variant_id=?',(existing['id'],existing['status'],now(),variant_id))
            return get(app,variant_id)
        try:
            result=app.enqueue(post['id'],{'version':post['version'],'channels':[channel],'publish_public':True})
            with app.db.connect(immediate=True) as db:db.execute("UPDATE distribution_intents SET job_id=?,status='queued',message='',updated_at=? WHERE variant_id=?",(result['ids'][0],now(),variant_id))
            return get(app,variant_id)
        except Problem as exc:
            details=exc.details or {}
            checks=details.get('checks',[]) if isinstance(details,dict) else []
            message='；'.join(c.get('message') or c.get('detail') or c.get('name','') for c in checks if c.get('level') in ('error','warn')) or str(exc)
    with app.db.connect(immediate=True) as db:db.execute("UPDATE distribution_intents SET status='blocked',message=?,updated_at=? WHERE variant_id=?",(message,now(),variant_id))
    return get(app,variant_id)
