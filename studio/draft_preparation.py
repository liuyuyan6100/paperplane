"""Prepare missing draft information as a reviewable proposal, never apply silently."""
import hashlib
import json
from pathlib import Path
import re
import threading

from .db import dumps,now,uid
from . import profiles,videos
from .adapters import DeliveryError

SCHEMA='''CREATE TABLE IF NOT EXISTS draft_preparations(
 id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(id),source_version INTEGER NOT NULL,
 status TEXT NOT NULL,snapshot TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',signature TEXT NOT NULL,
 error TEXT NOT NULL DEFAULT '',run_token TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS idx_draft_preparations_post ON draft_preparations(post_id,created_at);
 CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_preparations_active ON draft_preparations(post_id) WHERE status IN ('queued','running','review');'''
IMAGE=re.compile(r'!\[([^\]\n]*)\]\(([^)\n]+)\)')

def normalize_summary(value):
    summary=value.get('summary',value.get('摘要')) if isinstance(value,dict) else value if isinstance(value,str) else None
    if not isinstance(summary,str) or not summary.strip():raise DeliveryError('AI未返回可用的摘要建议，原稿保留')
    raw=summary.strip();summary=re.sub(r'\s+',' ',raw)
    if len(summary)>120:
        complete=[m.end() for m in re.finditer(r'[。！？!?；;]',summary[:120]) if m.end()>=30]
        if complete:summary=summary[:complete[-1]]
        else:
            clauses=[m.start() for m in re.finditer(r'[，,：:]',summary[:119]) if m.start()>=40]
            summary=summary[:clauses[-1]].rstrip()+'。' if clauses else summary[:119].rstrip()+'…'
    return summary,raw,summary!=raw

def local_file(app,url):
    if not isinstance(url,str) or not re.fullmatch(r'/media/[a-f0-9]{32}\.(?:png|jpg|gif|webp)',url):return None
    path=app.db.data_dir/'media'/Path(url).name
    return path if not path.is_symlink() and path.is_file() else None

def image_repairs(app,post):
    from .wechat import _visible_markdown
    visible=_visible_markdown(post['body']);repairs=[];pending=[];edits=[]
    for match in IMAGE.finditer(visible):
        alt,url=match.groups()
        if not url.startswith('/media/') or local_file(app,url):continue
        replacement=None
        if alt.strip() and sum(m[1]==alt for m in IMAGE.finditer(visible))==1:
            for row in app.db.rows('SELECT snapshot FROM revisions WHERE post_id=? AND version<? ORDER BY version DESC LIMIT 30',(post['id'],post['version'])):
                prior=[m[2] for m in IMAGE.finditer(_visible_markdown(json.loads(row['snapshot'])['body'])) if m[1]==alt and local_file(app,m[2])]
                if len(set(prior))==1:replacement=prior[0];break
        if replacement:
            repairs.append({'old':url,'new':replacement,'alt':alt,'reason':'本篇历史中相同图片说明的有效原图引用'})
            edits.append((match.start(2),match.end(2),replacement))
        else:pending.append('图片“'+(alt or '未命名')+'”原件未能确认，请重新选择；不会自动换成另一张图片')
    body=post['body']
    for start,end,replacement in reversed(edits):body=body[:start]+replacement+body[end:]
    return body,repairs,pending

def author_suggestion(app,post,profile):
    if post['author'].strip():return post['author'],'当前文章'
    if profile:
        names={r['author'].strip() for r in app.db.rows("SELECT author FROM posts WHERE profile_id=? AND author!='' AND archived=0 AND id!=?",(profile['id'],post['id'])) if 0<len(r['author'].strip())<=16}
        if len(names)==1:return names.pop(),'同一画像已保存文章的统一署名'
        footer=profile.get('wechat_footer','')
        name=re.split(r'[｜|\n]',footer,1)[0].strip()
        name=re.sub(r'^(?:作者|署名)[：:]\s*','',name)
        if footer and 0<len(name)<=16 and not re.search(r'https?://|更多|关注|博客',name):return name,'画像中的公众号文末署名'
        name=profile.get('name','').split('·')[0].strip()
        if 0<len(name)<=16:return name,'账号画像名称（请确认是否作为作者署名）'
    return '','尚无可核实的作者信息'

def fingerprint(app,post,extra=()):
    profile=profiles.get(app,post['profile_id']) if post.get('profile_id') else None
    refs={m[2] for m in IMAGE.finditer(post['body'])}|set(extra)|{post['cover']}
    files=[]
    for url in sorted(refs):
        path=local_file(app,url);stat=path.stat() if path else None
        files.append((url,stat.st_size if stat else None,stat.st_mtime_ns if stat else None))
    material=[(v['id'],v['version']) for v in videos.listing(app,post['id'])]
    return hashlib.sha256(dumps([post['version'],profile['version'] if profile else None,material,files,app.config.get('WECHAT_APP_ID')]).encode()).hexdigest()

class DraftPreparation:
    def __init__(self,app):
        self.app=app;self.db=app.db;self.lock=threading.Lock()
        with self.db.connect() as db:db.executescript(SCHEMA)
        self.interrupt()

    def interrupt(self):
        with self.db.connect(immediate=True) as db:db.execute("UPDATE draft_preparations SET status='interrupted',error='补齐准备已中断，已完成的素材回执保留，请手动重试',run_token='' WHERE status='running'")

    def latest(self,post_id):
        row=self.db.one('SELECT * FROM draft_preparations WHERE post_id=? ORDER BY rowid DESC LIMIT 1',(post_id,))
        if not row:return None
        result=json.loads(row['result']);snapshot=json.loads(row['snapshot']);post=self.app.post(post_id)
        from .model_provenance import public_for
        return {k:row[k] for k in ('id','post_id','source_version','status','error','created_at','updated_at')}|{'result':result,'source_changed':row['signature']!=fingerprint(self.app,post,[r['new'] for r in result.get('image_repairs',[])]),'model_provenance':public_for(self.app,'draft_preparations',row['id'])}

    def needed(self,post):
        if not post['summary'].strip() or len(post['summary'])>120 or not post['author'].strip():return True
        from .wechat import _visible_markdown
        if image_repairs(self.app,post)[1]:return True
        owned={v['id']:v for v in videos.listing(self.app,post['id'])}
        prior=self.latest(post['id']);attempted={v['id'] for v in (prior or {}).get('result',{}).get('videos',[]) if not v['ready']}
        return any(m[2] in owned and not videos.player_ready(owned[m[2]]) and m[2] not in attempted for m in videos.MARKER.finditer(_visible_markdown(post['body'])))

    def request(self,post_id,values=None):
        from .service import Problem
        values=values or {};post=self.app.post(post_id)
        if values.get('version',post['version'])!=post['version']:raise Problem('稿件已变化，请保存后重新准备',409)
        if post['archived'] or post['stage']=='published' or post.get('target_channel') not in ('','wechat'):raise Problem('请在未发表的公众号稿件中准备送稿信息',409)
        if self.app.article_flow.existing(post_id):raise Problem('本篇已有送稿记录，请先核对原草稿',409)
        prior=self.latest(post_id)
        if prior and prior['source_version']==post['version'] and not prior['source_changed']:
            if prior['status'] in ('queued','running','review','applied'):return prior
            if not values.get('retry'):return prior
        profile=profiles.get(self.app,post['profile_id']) if post.get('profile_id') else None
        with self.db.connect(immediate=True) as db:
            fresh=db.execute('SELECT version FROM posts WHERE id=?',(post_id,)).fetchone()
            if fresh['version']!=post['version']:raise Problem('稿件已变化，请重新准备',409)
            active=db.execute("SELECT id,source_version FROM draft_preparations WHERE post_id=? AND status IN ('queued','running','review')",(post_id,)).fetchone()
            if active and active['source_version']==post['version'] and prior and not prior['source_changed']:return prior
            db.execute("UPDATE draft_preparations SET status='stale' WHERE post_id=? AND status IN ('queued','running','review')",(post_id,))
            db.execute("INSERT INTO draft_preparations(id,post_id,source_version,status,snapshot,signature,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?,?)",(uid(),post_id,post['version'],dumps({'post':post,'profile':profile}),fingerprint(self.app,post),now(),now()))
        self.app.workflows._wake.set();return self.latest(post_id)

    def tick(self):
        if self.app.stop.is_set() or self.app.ai_lock.locked() or not self.lock.acquire(False):return False
        try:
            token=uid()
            with self.db.connect(immediate=True) as db:
                row=db.execute("SELECT * FROM draft_preparations WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
                if not row:return False
                db.execute("UPDATE draft_preparations SET status='running',run_token=?,updated_at=? WHERE id=?",(token,now(),row['id']))
            snapshot=json.loads(row['snapshot']);post=snapshot['post'];profile=snapshot['profile']
            try:
                current=self.app.post(post['id'])
                if current['archived'] or current['stage']=='published' or fingerprint(self.app,current)!=row['signature']:
                    with self.db.connect(immediate=True) as db:db.execute("UPDATE draft_preparations SET status='stale',updated_at=? WHERE id=? AND run_token=?",(now(),row['id'],token))
                    return True
                body,repairs,pending=image_repairs(self.app,post)
                from .writing_rules import apply_footer
                body,footer_added=apply_footer(body,profile,'wechat')
                author,source=author_suggestion(self.app,post,profile);summary=post['summary'];original_summary='';summary_shortened=False
                if not summary.strip() or len(summary)>120:
                    from .model_routing import route_for
                    route=route_for(self.app.config,'body',owner_kind='draft_preparations',owner_id=row['id'])
                    raw=self.app.generate_draft({'title':post['title'],'body':body,'summary':'','_profile_snapshot':profile,'_model_task':'body','_model_route':route,'_json_object':True},'仅根据本文生成用于公众号展示的中文摘要，60至100字，不超过120字。不要编造数据、亲历或视频分析，不把内部创作指令写入摘要。只输出JSON对象 {"summary":"摘要"}。',structured=True)
                    value=json.loads(raw)
                    summary,original_summary,summary_shortened=normalize_summary(value)
                state=self.db.one('SELECT status,run_token FROM draft_preparations WHERE id=?',(row['id'],))
                if not state or state['status']!='running' or state['run_token']!=token:return True
                prepared=[];owned={v['id']:v for v in videos.listing(self.app,post['id'])}
                from .wechat import _visible_markdown
                for match in videos.MARKER.finditer(_visible_markdown(body)):
                    if match[2] not in owned:pending.append('正文引用的视频不属于本篇，请重新选择');continue
                    video=owned[match[2]]
                    try:
                        # Permanent-material uploads target menus/replies.
                        # Never silently upload through that route for articles.
                        if not videos.player_ready(video):pending.append('图文视频尚未就绪：请在微信图文编辑页用“本地上传”上传成片并插入保存，再从本篇草稿关联；API素材上传不能代替这一步')
                        prepared.append({'id':video['id'],'title':video['title'],'status':video['status'],'ready':videos.player_ready(video)})
                    except DeliveryError as exc:
                        pending.append('视频“'+video['title']+'”：'+str(exc))
                        prepared.append({'id':video['id'],'title':video['title'],'status':'needs_check','ready':False})
                if not local_file(self.app,post['cover']):pending.append('封面原件未能确认，请选择已采用的封面素材')
                if not author:pending.append('作者信息尚未确定，请在确认页填写署名')
                current=self.app.post(post['id']);current_profile=profiles.get(self.app,current['profile_id']) if current.get('profile_id') else None
                unchanged=current['version']==post['version'] and (current_profile or {}).get('version')==(profile or {}).get('version') and current.get('profile_id')==post.get('profile_id')
                result={'summary':summary,'author':author,'author_source':source,'body':body,'cover':post['cover'],'image_repairs':repairs,'videos':prepared,'pending':list(dict.fromkeys(pending)),'footer_added':footer_added,'footer':profile.get('wechat_footer','') if footer_added else '','original_summary':original_summary,'summary_shortened':summary_shortened}
                sig=fingerprint(self.app,current,[r['new'] for r in repairs])
                with self.db.connect(immediate=True) as db:db.execute("UPDATE draft_preparations SET status=?,result=?,signature=?,updated_at=? WHERE id=? AND status='running' AND run_token=?",('review' if unchanged else 'stale',dumps(result),sig if unchanged else row['signature'],now(),row['id'],token))
            except Exception as exc:
                from .service import Problem
                with self.db.connect(immediate=True) as db:db.execute("UPDATE draft_preparations SET status='failed',error=?,updated_at=? WHERE id=? AND status='running' AND run_token=?",(str(exc) if isinstance(exc,(Problem,DeliveryError)) else '自动补齐未完成，请重试；原稿保留',now(),row['id'],token))
            return True
        finally:self.lock.release()

    def confirm(self,preparation_id,values):
        from .service import Problem,text_value
        with self.app.article_flow.lock:
            row=self.db.one('SELECT * FROM draft_preparations WHERE id=?',(preparation_id,))
            if not row:raise Problem('补齐建议不存在',404)
            post=self.app.post(row['post_id']);result=json.loads(row['result'])
            if row['status']=='applied':
                confirmed=result.get('confirmed',{})
                if any(key in values and values[key]!=confirmed.get(key) for key in ('summary','author')):raise Problem('这份补齐建议已经确认保存，请在当前文章中继续修改',409)
                return self.app.article_flow.get(post['id'])
            if row['status']!='review' or values.get('version')!=post['version'] or row['source_version']!=post['version'] or fingerprint(self.app,post,[r['new'] for r in result.get('image_repairs',[])])!=row['signature']:raise Problem('文章或素材已变化，建议已保留，请基于当前稿重新准备',409)
            summary=text_value(values.get('summary',result['summary']),'摘要',120,True)
            author=text_value(values.get('author',result['author']),'作者',16,True)
            saved=self.app.save_post({'version':post['version'],'summary':summary,'author':author,'body':result['body'],'_edit_origin':'generated'},post['id'])
            result['confirmed']={'summary':summary,'author':author,'post_version':saved['version']}
            with self.db.connect(immediate=True) as db:
                db.execute("UPDATE draft_preparations SET status='applied',result=?,updated_at=? WHERE id=?",(dumps(result),now(),row['id']))
                db.execute("UPDATE article_draft_intents SET checked_version=? WHERE post_id=?",(saved['version'],post['id']))
            intent=self.db.one('SELECT status FROM article_draft_intents WHERE post_id=?',(post['id'],))
            if intent and intent['status']=='waiting':self.app.article_flow._run(post['id'])
            return self.app.article_flow.get(post['id'])
