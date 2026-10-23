"""Article-local next steps and opt-in, resumable WeChat draft preparation.

This coordinator never publishes publicly or replaces an existing platform draft.
It retains explicit intent only while required information is being corrected.
"""
import json
import threading
from .db import dumps, now
from .model_provenance import no_model

SCHEMA = """
CREATE TABLE IF NOT EXISTS article_draft_intents (
 post_id TEXT PRIMARY KEY REFERENCES posts(id), channel_id TEXT NOT NULL DEFAULT 'wechat',
 status TEXT NOT NULL DEFAULT 'waiting', checked_version INTEGER NOT NULL DEFAULT 0,
 job_id TEXT NOT NULL DEFAULT '', report TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
);
"""


class ArticleFlow:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        with app.db.connect(immediate=True) as db:
            for statement in SCHEMA.split(';'):
                if statement.strip(): db.execute(statement)
            db.execute("UPDATE article_draft_intents SET status='waiting',checked_version=0 WHERE status='checking'")

    def existing(self, post_id):
        return self.app.db.one("SELECT id,status,version,error,note FROM jobs WHERE post_id=? AND channel_id='wechat' AND status!='canceled' ORDER BY created_at DESC,rowid DESC LIMIT 1", (post_id,)) or self.app.db.one("SELECT job_id AS id,status,version,'' AS error,'记录已清理，保留送稿历史' AS note FROM delivery_tombstones WHERE post_id=? AND channel_id='wechat' AND status!='canceled' ORDER BY purged_at DESC LIMIT 1", (post_id,))

    def get(self, post_id):
        post = self.app.post(post_id)
        intent = self.app.db.one('SELECT * FROM article_draft_intents WHERE post_id=?', (post_id,))
        if intent: intent['report'] = json.loads(intent['report'])
        job = self.existing(post_id)
        review = self.app.db.one('SELECT id,status,source_version,applied_version FROM editorial_reviews WHERE post_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (post_id,))
        if review:
            review['stale'] = post['version'] not in (review['source_version'], review['applied_version'])
        workflow = self.app.db.one('SELECT id,status,stage,applied_version FROM workflows WHERE post_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (post_id,))
        plans = self.app.db.rows("SELECT id,source_version,latest_post_version,status FROM illustration_plans WHERE post_id=? AND archived_at='' ORDER BY created_at DESC,rowid DESC", (post_id,))
        for plan in plans: plan['stale'] = post['version'] not in (plan['source_version'], plan['latest_post_version'])
        missing = [label for key, label in (('summary','摘要'),('author','作者'),('cover','封面')) if not post[key].strip()]
        if job:
            labels = {'queued':'已进入送稿队列', 'running':'正在送入公众号草稿箱', 'draft':'已送入公众号草稿箱', 'uncertain':'送稿结果待核对', 'failed':'送稿失败，请查看执行记录', 'published':'已登记公开发表', 'manual':'等待在平台完成'}
            next_step = labels.get(job['status'], '查看公众号送稿记录')
            if job['version'] != post['version']:
                next_step += f"；该记录使用 v{job['version']}，当前为 v{post['version']}"
            action = 'job'
        elif workflow and workflow['status'] in ('queued','running','outline_review','review'):
            next_step = '查看创作任务，确认提纲或采用待审正文'
            action = 'workflow'
        elif post['stage']=='idea' or not post['body'].strip():
            next_step, action = '生成提纲，确认后再写正文', 'workflow-open'
        elif intent and intent['status'] in ('waiting','checking'):
            next_step, action = '补齐下方问题并保存，纸飞机会自动复查并继续送稿', 'waiting'
        elif missing:
            next_step, action = '补齐' + '、'.join(missing) + '，再检查并送入公众号草稿箱', 'metadata'
        else:
            next_step, action = '检查图文并送入公众号草稿箱', 'wechat-draft'
        stages = []
        if workflow:
            stages.extend(self.app.workflows.get(workflow['id']).get('model_stages', []))
        if review:
            stages.append(self.app.reviews.get(review['id']).get('model_provenance', {}))
        preparation=self.app.preparation.latest(post_id) if hasattr(self.app,'preparation') else None
        if preparation and not preparation['source_changed'] and not job:
            if preparation['status'] in ('queued','running'):next_step,action='正在自动补齐摘要、署名并核对图文视频素材','preparing'
            elif preparation['status']=='review':next_step,action='补齐建议已准备好，请确认后继续检查送稿','preparation-review'
        return {'post_id':post_id, 'post_version':post['version'], 'target_channel':'wechat','preparation':preparation,
                'next_step':next_step, 'action':action, 'workflow':workflow, 'review':review,
                'plans':plans, 'missing':missing, 'intent':intent, 'job':job,
                'models':stages, 'delivery_model':no_model('delivery')}

    def request(self, post_id, values):
        from .service import Problem
        if not self.lock.acquire(blocking=False): raise Problem('正在检查送稿，请等待完成',409)
        try:
            post = self.app.post(post_id)
            if values.get('version') != post['version']: raise Problem('稿件已变化，请保存后再检查',409)
            if post['archived']: raise Problem('文章已归档，请先恢复',409)
            if post.get('target_channel') and post['target_channel'] != 'wechat': raise Problem('此平台稿属于其他渠道，请使用对应平台的送稿入口',409)
            if post['stage'] == 'published': raise Problem('已发表的文章不会自动再次送稿，请先查看原发表记录',409)
            if self.existing(post_id): return self.get(post_id)
            with self.app.db.connect(immediate=True) as db:
                db.execute("INSERT INTO article_draft_intents(post_id,status,checked_version,updated_at) VALUES(?,'waiting',0,?) ON CONFLICT(post_id) DO UPDATE SET status='waiting',checked_version=0,job_id='',report='{}',updated_at=excluded.updated_at", (post_id,now()))
            self._run(post_id)
            return self.get(post_id)
        finally: self.lock.release()

    def cancel(self, post_id, values):
        from .service import Problem
        with self.lock:
            post = self.app.post(post_id)
            if values.get('version') != post['version']: raise Problem('稿件已变化，请刷新后再操作',409)
            if self.existing(post_id): raise Problem('送稿任务已经创建，请到执行记录查看或取消尚未执行的任务',409)
            with self.app.db.connect(immediate=True) as db:
                db.execute("UPDATE article_draft_intents SET status='canceled',updated_at=? WHERE post_id=? AND status='waiting'", (now(),post_id))
                db.execute("UPDATE draft_preparations SET status='canceled' WHERE post_id=? AND status IN ('queued','running','review')",(post_id,))
        return self.get(post_id)

    def settings_changed(self):
        with self.app.db.connect(immediate=True) as db:
            db.execute("UPDATE article_draft_intents SET checked_version=0 WHERE status='waiting'")
        self.app.wake.set()

    def resume_due(self):
        if not self.lock.acquire(blocking=False): return
        try:
            rows = self.app.db.rows("SELECT i.post_id FROM article_draft_intents i JOIN posts p ON p.id=i.post_id WHERE i.status='waiting' AND p.archived=0 AND (i.checked_version!=p.version OR (NOT EXISTS(SELECT 1 FROM draft_preparations d WHERE d.post_id=p.id) AND (p.summary='' OR p.author=''))) LIMIT 10")
            for row in rows:
                if self.app.stop.is_set(): break
                self._run(row['post_id'])
        finally: self.lock.release()

    def _store(self, post_id, status, version, report, job_id=''):
        with self.app.db.connect(immediate=True) as db:
            db.execute('UPDATE article_draft_intents SET status=?,checked_version=?,report=?,job_id=?,updated_at=? WHERE post_id=?', (status,version,dumps(report),job_id,now(),post_id))

    def check(self, post):
        """Read-only checks shared by the preview and the delivery gate."""
        report = self.app.preflight(post,['wechat'])
        for key, label in (('summary','摘要'),('author','作者')):
            if not post[key].strip(): report['checks'].append({'label':label,'level':'error','detail':'请在文章信息中补齐' + label + '并保存'})
        channel = next((c for c in self.app.channels() if c['id']=='wechat'),None)
        if not channel or channel['kind'] != 'wechat':
            report['checks'].append({'label':'公众号自动送稿','level':'error','detail':'请在渠道管理中配置公众号 API 送稿'})
        # Ambiguous content is not silently approved as a formatting warning.
        for check in report['checks']:
            if check['level']=='warn' and check['label'] in ('待核实内容','母稿版本'):
                check['level']='error'
        report['ok'] = not any(c['level']=='error' for c in report['checks'])
        return report

    def _run(self, post_id):
        from .service import Problem
        post = self.app.post(post_id)
        old_job = self.existing(post_id)
        if old_job:
            self._store(post_id,'existing',post['version'],{},old_job['id']); return
        if post['archived'] or post['stage']=='published':
            self._store(post_id,'canceled',post['version'],{}); return
        self._store(post_id,'checking',post['version'],{})
        try:
            if not post['body'].strip():
                raise Problem('请先完成正文并保存')
            if post['stage'] != 'ready':
                post = self.app.save_post({'version':post['version'],'stage':'ready'},post_id)
            post = self.app.reviews.format(post_id, {'post_version':post['version']})['post']
            with self.app.db.connect(immediate=True) as db:db.execute("UPDATE draft_preparations SET status='stale' WHERE post_id=? AND source_version!=? AND status IN ('queued','running','review')",(post_id,post['version']))
            report = self.check(post)
            if not report['ok']:
                self._store(post_id,'waiting',post['version'],report)
                if self.app.preparation.needed(post):self.app.preparation.request(post_id,{'version':post['version']})
                return
            result = self.app.enqueue(post_id,{'version':post['version'],'channels':['wechat'],'ack_warnings':True,'_article_flow':True})
            self._store(post_id,'queued',self.app.post(post_id)['version'],report,result['ids'][0])
        except Problem as exc:
            report = exc.details if isinstance(exc.details,dict) and 'checks' in exc.details else {'ok':False,'checks':[{'label':'送稿准备','level':'error','detail':str(exc)}]}
            self._store(post_id,'waiting',post['version'],report)
        except Exception:
            # No send occurred here: actual platform I/O is always performed by jobs.
            self._store(post_id,'waiting',post['version'],{'ok':False,'checks':[{'label':'送稿准备','level':'error','detail':'检查中断，请查看稿件和已有送稿记录后重试'}]})
