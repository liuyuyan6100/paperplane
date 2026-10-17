"""Article-scoped video evidence and explicit WeChat material preparation."""
import base64
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit,unquote

from .db import dumps, now, uid
from .adapters import DeliveryError

MAX_BYTES=10*1024*1024
MARKER=re.compile(r'^@\[([^\]\n]{0,120})\]\(/video/([a-f0-9]{32})\.mp4\)[ \t]*$',re.M)
SCHEMA='''CREATE TABLE IF NOT EXISTS article_videos(
 id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(id),title TEXT NOT NULL,
 description TEXT NOT NULL,filename TEXT NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,
 version INTEGER NOT NULL DEFAULT 1,wechat_media_id TEXT NOT NULL DEFAULT '',
 wechat_app_id TEXT NOT NULL DEFAULT '',embed_url TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'local',error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS idx_article_videos_post ON article_videos(post_id);'''

def init(app):
    with app.db.connect() as db:db.executescript(SCHEMA)
    with app.db.connect(immediate=True) as db:
        if 'native_player' not in {r['name'] for r in db.execute('PRAGMA table_info(article_videos)')}:
            db.execute("ALTER TABLE article_videos ADD COLUMN native_player TEXT NOT NULL DEFAULT '{}'")
    (app.db.data_dir/'videos').mkdir(exist_ok=True)
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE article_videos SET status='uncertain',error='上次视频上传已中断，请先到公众号素材库核对，勿重复上传' WHERE status='uploading'")

def public(row):
    return {k:row[k] for k in ('id','post_id','title','description','filename','sha256','bytes','version','status','error','embed_url','wechat_app_id')} | {'url':'/video/'+row['id']+'.mp4','wechat_uploaded':bool(row['wechat_media_id']),'player_ready':player_ready(row),'native_player':json.loads(dict(row).get('native_player','{}'))}

def native_players(content):
    """Extract only the known WeChat native card fields, never arbitrary HTML."""
    class Parser(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.items=[]
        def handle_starttag(self,tag,attrs):
            if tag!='iframe':return
            attrs=dict(attrs);info=player(attrs.get('data-src') or attrs.get('src') or '')
            if not info or info[0]!='wechat' or not info[1].startswith('wxv_') or attrs.get('data-mpvid')!=info[1] or attrs.get('data-vidtype')!='2':return
            try:
                width=int(attrs.get('data-w',''));ratio=float(attrs.get('data-ratio',''))
                if not 16<=width<=8192 or not .1<=ratio<=10:return
                cover=attrs.get('data-cover','');url=urlsplit(unquote(cover))
                if url.scheme not in ('http','https') or url.hostname not in ('mmbiz.qpic.cn','mmbiz.qlogo.cn') or url.username or url.password or url.port not in (None,80,443):return
            except (ValueError,TypeError):return
            self.items.append({'vid':info[1],'cover':cover,'width':width,'ratio':str(ratio)})
    parser=Parser();parser.feed(content);return parser.items

def associate_draft(app,video_id,values):
    from .wechat import WeChatClient
    from .service import Problem
    row=get(app,video_id);post=app.post(row['post_id']);account=app.config.get('WECHAT_APP_ID')
    if values.get('version')!=row['version']:raise Problem('视频说明已更新，请刷新后关联',409)
    if row['wechat_app_id'] and row['wechat_app_id']!=account:raise Problem('该素材属于另一公众号',409)
    job=app.db.one("SELECT * FROM jobs WHERE post_id=? AND channel_id='wechat' AND status IN ('draft','uncertain') ORDER BY created_at DESC,rowid DESC LIMIT 1",(post['id'],))
    if not job or not job['external_id']:raise Problem('本篇尚无可回读的微信草稿，请先在微信插入并保存视频')
    from .wechat import _visible_markdown
    references=[m[2] for m in MARKER.finditer(_visible_markdown(post['body']))]
    if references!=[video_id]:raise Problem('本篇引用多个视频或尚未插入本视频，请明确对应关系后再关联')
    result=WeChatClient(app.config).api('/cgi-bin/draft/get',{'media_id':job['external_id']})
    articles=result.get('news_item',[])
    if len(articles)!=1 or articles[0].get('title')!=json.loads(job['snapshot'])['title']:raise Problem('草稿标题或篇数已变化，请先核对原草稿')
    items=native_players(articles[0].get('content',''))
    if len(items)!=1:raise Problem('草稿未包含唯一可识别的微信原生视频，请在微信插入并保存后重试')
    native=items[0]|{'source':'wechat-draft-readback','draft_id':job['external_id'],'verified_at':now()}
    with app.db.connect(immediate=True) as db:
        fresh=db.execute('SELECT version FROM posts WHERE id=?',(post['id'],)).fetchone()
        current=db.execute('SELECT external_id,status FROM jobs WHERE id=?',(job['id'],)).fetchone()
        if fresh['version']!=post['version'] or current['external_id']!=job['external_id'] or current['status']!=job['status'] or app.config.get('WECHAT_APP_ID')!=account:raise Problem('文章或草稿归属已变化，请重新核对',409)
        changed=db.execute("UPDATE article_videos SET embed_url=?,native_player=?,wechat_app_id=?,error='',version=version+1,updated_at=? WHERE id=? AND version=?",('https://mp.weixin.qq.com/mp/video?'+urlencode({'vid':native['vid']}),dumps(native),account,now(),video_id,row['version'])).rowcount
        if changed!=1:raise Problem('视频已变化，请刷新后重新关联',409)
    return public(get(app,video_id))

def player_ready(item):
    item=dict(item);info=player(item.get('embed_url',''))
    if not info:return False
    if info[0]=='wechat' and info[1].startswith('apiv_'):
        # These uploads are labelled for menus/replies and disabled by the
        # article editor. Retaining HTML is not proof of article eligibility.
        return False
    return True

def native_card(native):
    vid=str(native.get('vid',''))
    url='https://mp.weixin.qq.com/mp/readtemplate?'+urlencode({'t':'pages/video_player_tmpl','action':'mpvideo','auto':0,'vid':vid})
    return f'<iframe class="video_iframe rich_pages" data-src="{html.escape(url,quote=True)}" data-mpvid="{html.escape(vid,quote=True)}" data-vidtype="2" data-cover="{html.escape(str(native.get("cover","")),quote=True)}" data-ratio="{html.escape(str(native.get("ratio","")),quote=True)}" data-w="{html.escape(str(native.get("width","")),quote=True)}"></iframe>'

def valid_native(native):return len(native_players(native_card(native)))==1

def listing(app,post_id,db=None):
    if db is None:
        with app.db.connect() as db:return listing(app,post_id,db)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='article_videos'").fetchone():return []
    return [public(r) for r in db.execute('SELECT * FROM article_videos WHERE post_id=? ORDER BY created_at,id',(post_id,))]

def get(app,video_id):
    row=app.db.one('SELECT * FROM article_videos WHERE id=?',(video_id,))
    if not row:raise DeliveryError('视频素材不存在')
    return row

def mp4_container(raw):
    offset=0;boxes=set();count=0
    while offset<len(raw):
        if offset+8>len(raw) or count>1000:return False
        size=int.from_bytes(raw[offset:offset+4],'big');kind=raw[offset+4:offset+8];header=8
        if size==1:
            if offset+16>len(raw):return False
            size=int.from_bytes(raw[offset+8:offset+16],'big');header=16
        elif size==0:size=len(raw)-offset
        if size<header or offset+size>len(raw):return False
        if size>header:boxes.add(kind)
        offset+=size;count+=1
    return raw[4:8]==b'ftyp' and {b'ftyp',b'moov',b'mdat'}.issubset(boxes)

def upload(app,post_id,values):
    from .service import text_value,Problem
    app.post(post_id)
    title=text_value(values.get('title',''),'视频标题',120,True)
    description=text_value(values.get('description',''),'视频说明',4000,True)
    filename=text_value(values.get('filename','video.mp4'),'视频文件名',180,True)
    if not filename.lower().endswith('.mp4') or any(c in filename for c in '/\\\r\n'):
        raise Problem('请选择MP4文件，文件名不能包含路径')
    try:raw=base64.b64decode(values.get('data',''),validate=True)
    except (ValueError,TypeError):raise Problem('视频编码不正确') from None
    if not 16<=len(raw)<MAX_BYTES or not mp4_container(raw):raise Problem('视频须为小于10 MiB的有效MP4文件')
    digest=hashlib.sha256(raw).hexdigest()
    prior=app.db.one('SELECT * FROM article_videos WHERE post_id=? AND sha256=?',(post_id,digest))
    if prior:return public(prior)
    video_id=uid();file=app.db.data_dir/'videos'/(video_id+'.mp4')
    file.write_bytes(raw)
    try:
        with app.db.connect(immediate=True) as db:
            db.execute('INSERT INTO article_videos(id,post_id,title,description,filename,sha256,bytes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(video_id,post_id,title,description,filename,digest,len(raw),now(),now()))
    except Exception:
        file.unlink(missing_ok=True);raise
    return public(get(app,video_id))

def player(value):
    """Only supported platform identities, never arbitrary iframe HTML/URLs."""
    try:
        p=urlsplit(value.strip());q=parse_qs(p.query)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):return None
        vid=q.get('vid',[''])[0]
        if p.hostname=='v.qq.com':
            match=re.fullmatch(r'/x/(?:page/([A-Za-z0-9]{11})|cover/[A-Za-z0-9]+/([A-Za-z0-9]{11}))\.html',p.path)
            vid=next((x for x in match.groups() if x),vid) if match else vid
            if not re.fullmatch(r'[A-Za-z0-9]{11}',vid):return None
            return 'tencent',vid,'https://v.qq.com/iframe/player.html?'+urlencode({'vid':vid,'auto':0})
        if p.hostname=='mp.weixin.qq.com' and p.path in ('/mp/video','/mp/readtemplate') and re.fullmatch(r'(?:wxv|apiv)_[0-9]{6,30}',vid):
            return 'wechat',vid,'https://mp.weixin.qq.com/mp/readtemplate?'+urlencode({'t':'pages/video_player_tmpl','vid':vid})
    except (ValueError,AttributeError):pass
    return None

def update(app,video_id,values):
    from .service import text_value,Problem
    with app.db.connect(immediate=True) as db:
        row=db.execute('SELECT * FROM article_videos WHERE id=?',(video_id,)).fetchone()
        if not row:raise Problem('视频不存在',404)
        if values.get('version')!=row['version']:raise Problem('视频说明已更新，请刷新后重试',409)
        title=text_value(values.get('title',row['title']),'视频标题',120,True)
        description=text_value(values.get('description',row['description']),'视频说明',4000,True)
        link=text_value(values.get('embed_url',row['embed_url']),'微信或腾讯视频链接',2000)
        if link and not player(link):raise Problem('请使用含vid的公众号视频链接或腾讯视频播放页，不能粘贴HTML代码')
        db.execute('UPDATE article_videos SET title=?,description=?,embed_url=?,version=version+1,updated_at=? WHERE id=?',(title,description,link,now(),video_id))
    return public(get(app,video_id))

def prepare_wechat(app,video_id):
    from .wechat import WeChatClient
    row=get(app,video_id);identity=app.config.get('WECHAT_APP_ID')
    if row['wechat_media_id']:
        if row['wechat_app_id']!=identity:raise DeliveryError('该视频已关联另一公众号，请在对应账号中核对')
        if not row['embed_url']:
            detail=WeChatClient(app.config,timeout=60).api('/cgi-bin/material/get_material',{'media_id':row['wechat_media_id']})
            vid=detail.get('vid','')
            if isinstance(vid,str) and re.fullmatch(r'(?:wxv|apiv)_[0-9]{6,30}',vid):
                with app.db.connect(immediate=True) as db:db.execute("UPDATE article_videos SET embed_url=?,error='',version=version+1,updated_at=? WHERE id=?",('https://mp.weixin.qq.com/mp/video?'+urlencode({'vid':vid}),now(),video_id))
                row=get(app,video_id)
        return public(row)
    if row['status'] in ('uploading','uncertain'):raise DeliveryError('视频上传结果尚待核对，请先到公众号素材库检查，勿重复上传')
    file=app.db.data_dir/'videos'/(video_id+'.mp4');raw=file.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise DeliveryError('视频文件已变化，请重新导入')
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE article_videos SET status='uploading',error='',updated_at=? WHERE id=? AND status NOT IN ('uploading','uncertain')",(now(),video_id))
        if not db.execute('SELECT changes()').fetchone()[0]:raise DeliveryError('已有视频上传正在处理')
    try:
        result=WeChatClient(app.config,timeout=180).api('/cgi-bin/material/add_material',image=(raw,'.mp4','video/mp4'),description={'title':row['title'],'introduction':row['description'][:1000]},query={'type':'video'},creating=True)
        media_id=result.get('media_id')
        if not isinstance(media_id,str) or not media_id:raise DeliveryError('视频上传未返回素材编号，请到公众号素材库核对',True)
        # Save acceptance before any subsequent read, so a readback interruption
        # can never turn into another upload of the same video.
        with app.db.connect(immediate=True) as db:
            db.execute("UPDATE article_videos SET wechat_media_id=?,wechat_app_id=?,status='uploaded',updated_at=? WHERE id=?",(media_id,identity,now(),video_id))
        link=result.get('url','') if player(result.get('url','')) else ''
        # The permanent-material upload returns a media_id, not a playable URL.
        # Its official readback supplies the native apiv_/wxv_ video identity.
        readback_error=''
        if not link:
            try:
                detail=WeChatClient(app.config,timeout=60).api('/cgi-bin/material/get_material',{'media_id':media_id})
                vid=detail.get('vid','')
                if isinstance(vid,str) and re.fullmatch(r'(?:wxv|apiv)_[0-9]{6,30}',vid):
                    link='https://mp.weixin.qq.com/mp/video?'+urlencode({'vid':vid})
            except DeliveryError:
                readback_error='视频素材已上传，播放器信息读取未完成；请核对公众号素材库'
        with app.db.connect(immediate=True) as db:
            db.execute("UPDATE article_videos SET wechat_media_id=?,wechat_app_id=?,embed_url=CASE WHEN embed_url='' THEN ? ELSE embed_url END,status='uploaded',error=?,version=version+1,updated_at=? WHERE id=?",(media_id,identity,link,readback_error,now(),video_id))
    except DeliveryError as exc:
        with app.db.connect(immediate=True) as db:db.execute('UPDATE article_videos SET status=?,error=?,updated_at=? WHERE id=?',('uncertain' if exc.uncertain else 'local',str(exc),now(),video_id))
        raise
    except Exception:
        with app.db.connect(immediate=True) as db:db.execute("UPDATE article_videos SET status='uncertain',error='视频上传结果未知，请到公众号素材库核对',updated_at=? WHERE id=?",(now(),video_id))
        raise DeliveryError('视频上传结果未知，请先核对公众号素材库，勿重复上传',True) from None
    return public(get(app,video_id))

def evidence(items):
    return [{k:v[k] for k in ('id','title','description','version','url')} for v in items]

def associate_material(app,video_id,values):
    """Reuse an existing owner-account upload only after official material readback."""
    from .wechat import WeChatClient
    from .service import text_value,Problem
    row=get(app,video_id)
    if row['status']=='uploading':raise Problem('视频正在上传，请等待完成',409)
    if row['wechat_app_id'] and row['wechat_app_id']!=app.config.get('WECHAT_APP_ID'):
        raise Problem('视频已关联另一公众号，请先切回原账号',409)
    media_id=text_value(values.get('media_id',''),'视频素材编号',512,True)
    detail=WeChatClient(app.config,timeout=60).api('/cgi-bin/material/get_material',{'media_id':media_id})
    vid=detail.get('vid','')
    if not isinstance(vid,str) or not re.fullmatch(r'(?:wxv|apiv)_[0-9]{6,30}',vid):raise Problem('公众号素材回执缺少可识别的视频编号，未建立关联')
    with app.db.connect(immediate=True) as db:
        db.execute("UPDATE article_videos SET wechat_media_id=?,wechat_app_id=?,embed_url=?,status='uploaded',error='',version=version+1,updated_at=? WHERE id=?",(media_id,app.config.get('WECHAT_APP_ID'),'https://mp.weixin.qq.com/mp/video?'+urlencode({'vid':vid}),now(),video_id))
    return public(get(app,video_id))

def snapshot_for_post(post,data_dir):
    if '_videos' in post:return post['_videos']
    p=Path(data_dir)/'studio.sqlite3'
    if not p.exists():return []
    with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='article_videos'").fetchone():return []
        return [public(r) for r in db.execute('SELECT * FROM article_videos WHERE post_id=?',(post.get('id',''),))]

def wechat_html(item):
    info=player(item.get('embed_url',''))
    if not info:raise DeliveryError('正文视频尚未关联公众号播放器，请先完成自动补齐准备；上传结果待核对时，请到素材库核实，勿重复上传')
    provider,vid,url=info
    if provider=='wechat':
        if vid.startswith('apiv_'):
            raise DeliveryError('这是用于自定义菜单、自动回复等场景的API视频素材，当前不能作为图文原生视频；请在微信图文编辑页用“本地上传”上传成片并插入，已暂停自动送稿')
        # mpvideo is an editor custom element, not a standalone player. The
        # draft API preserves it verbatim, but the reader can render it empty.
        url='https://mp.weixin.qq.com/mp/readtemplate?'+urlencode({'t':'pages/video_player_tmpl','action':'mpvideo','auto':0,'vid':vid})
        safe=html.escape(url,quote=True)
        native=item.get('native_player') or {}
        if isinstance(native,str):native=json.loads(native)
        if native.get('vid')==vid:
            if not valid_native(native):raise DeliveryError('原生视频关联信息不完整，请从微信草稿重新核对')
            return native_card(native)
        return f'<iframe class="video_iframe rich_pages" data-vidtype="2" data-mpvid="{vid}" data-vid="{vid}" src="{safe}" data-src="{safe}" frameborder="0" allowfullscreen="true" width="100%" height="360" style="display:block;width:100%;max-width:100%;height:360px;border:0;"></iframe>'
    return f'<iframe class="video_iframe" data-vid="{vid}" src="{html.escape(url,quote=True)}" data-src="{html.escape(url,quote=True)}" frameborder="0" allowfullscreen="true" width="100%" height="360"></iframe>'
