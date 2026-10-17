"""Version-bound visual drafts generated before adoption of platform text."""
import io,json,os,re,threading,hashlib
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from .db import dumps,now,uid
from .workflows import fail,identifier

SCHEMA='''
CREATE TABLE IF NOT EXISTS platform_visual_sets (
 variant_id TEXT PRIMARY KEY REFERENCES repurpose_variants(id), mode TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'waiting', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS platform_visual_pages (
 id TEXT PRIMARY KEY, variant_id TEXT NOT NULL REFERENCES repurpose_variants(id),
 version INTEGER NOT NULL, page_index INTEGER NOT NULL, page TEXT NOT NULL,
 status TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', token TEXT NOT NULL DEFAULT '',
 UNIQUE(variant_id,version,page_index)
);
'''

def plain(text):
    text=re.sub(r'```.*?```','',text,flags=re.S)
    text=re.sub(r'!\[[^\]]*\]\([^)]*\)','',text)
    text=re.sub(r'\[([^]]+)\]\([^)]*\)',r'\1',text)
    return re.sub(r'[#*`_>]','',text).strip()

def render_card(page,platform,index,total):
    portrait=page['slot']=='xhs';width,height=(1080,1440) if portrait else (1536,1024)
    im=Image.new('RGB',(width,height),'#f8f4ec');draw=ImageDraw.Draw(im)
    font_path=next((p for p in [Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc',Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')] if p.is_file()),None)
    if not font_path:fail('缺少中文字体，无法生成图卡；请安装微软雅黑或Noto Sans CJK字体')
    def fit(text,box,size,color):
        x,y,w,h=box
        for px in range(size,23,-2):
            font=ImageFont.truetype(str(font_path),px);lines=[]
            for paragraph in text.splitlines():
                line=''
                for char in paragraph:
                    if draw.textlength(line+char,font=font)>w:
                        lines.append(line);line=char
                    else:line+=char
                lines.append(line)
            spacing=int(px*1.55)
            if len(lines)*spacing<=h:break
        else:fail('图卡文字过长，请精简对应平台稿后重新生成')
        for line in lines:draw.text((x,y),line,font=font,fill=color);y+=spacing
    accent={'csdn':'#c85a37','juejin':'#315fd5','devto':'#2c3d43','xiaohongshu':'#bf4259'}.get(platform, '#315fd5')
    draw.rounded_rectangle((50,50,width-50,height-50),radius=28,outline='#ddd4c4',width=3)
    draw.rectangle((90,100,175,112),fill=accent)
    fit(__import__('studio.platform_catalog', fromlist=['REPURPOSE']).REPURPOSE[platform],(200,85,width-300,60),28,accent)
    title_height=460 if portrait else 390
    fit(page['title'],(90,210,width-180,title_height),76 if portrait else 74,'#203637')
    fit(page['text'],(90,730 if portrait else 650,width-180,490 if portrait else 230),44 if portrait else 38,'#415555')
    fit(f'{index+1:02d} / {total:02d}',(90,height-140,width-180,55),28,accent)
    out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()

class DeliverableManager:
    def __init__(self,app):
        self.app,self.db=app,app.db;self.lock=threading.Lock()
        with self.db.connect() as db:db.executescript(SCHEMA)
        self.interrupt()

    def interrupt(self):
        with self.db.connect(immediate=True) as db:
            db.execute("UPDATE platform_visual_pages SET status='interrupted',token='',error='图片生成已中断，请重试未完成图片' WHERE status IN ('queued','running')")

    def enable(self,variant_id,mode=None,retry=False):
        saved=self.db.one('SELECT mode FROM platform_visual_sets WHERE variant_id=?',(variant_id,))
        mode=mode or (saved['mode'] if saved else 'ai')
        if mode not in ('ai','cards'):fail('请选择 AI 配图或排版图卡')
        variant=self.app.repurpose.get(identifier(variant_id))
        with self.db.connect(immediate=True) as db:
            existing=db.execute('SELECT * FROM platform_visual_sets WHERE variant_id=?',(variant_id,)).fetchone()
            if existing and existing['mode']!=mode:fail('本批图片方式已固定，请保留本批结果后另建改编')
            db.execute('INSERT OR IGNORE INTO platform_visual_sets(variant_id,mode) VALUES(?,?)',(variant_id,mode))
            if retry:
                for page in db.execute("SELECT id,url FROM platform_visual_pages WHERE variant_id=? AND version=? AND status='ready'",(variant_id,variant['version'])).fetchall():
                    if not (self.db.data_dir/'media'/Path(page['url']).name).is_file():
                        db.execute("UPDATE platform_visual_pages SET status='queued',url='',error='' WHERE id=?",(page['id'],))
                db.execute("UPDATE platform_visual_pages SET status='queued',error='' WHERE variant_id=? AND version=? AND status IN ('failed','interrupted')",(variant_id,variant['version']))
                db.execute("UPDATE platform_visual_sets SET status='waiting',error='' WHERE variant_id=?",(variant_id,))
        self.app.workflows._wake.set()
        return self.get(variant_id,variant['version'])

    def get(self,variant_id,version):
        bundle=self.db.one('SELECT * FROM platform_visual_sets WHERE variant_id=?',(variant_id,))
        if not bundle:return {'status':'not_started','pages':[],'mode':'cards'}
        pages=self.db.rows('SELECT id,page_index,page,status,url,error FROM platform_visual_pages WHERE variant_id=? AND version=? ORDER BY page_index',(variant_id,version))
        for p in pages:
            p.update(json.loads(p.pop('page')))
            from .model_provenance import public_for, planned
            task = 'cover' if p['slot']=='cover' or p['page_index']==0 else 'illustration'
            p['model_provenance'] = public_for(self.app, 'platform_visual_pages', p['id'],
                route=p.get('model_route'), task=task, origin='layout' if bundle['mode']=='cards' else '')
            if bundle['mode']=='ai' and p['status']=='queued' and p['model_provenance']['source']=='unknown':
                p['model_provenance'] = planned(self.app.config, task)
            if p['status']=='ready' and not (self.db.data_dir/'media'/Path(p['url']).name).is_file():
                p.update(status='failed',url='',error='图片文件缺失，请重试此图片')
        status='waiting'
        if pages:
            status='ready' if all(p['status']=='ready' for p in pages) else 'failed' if any(p['status'] in ('failed','interrupted') for p in pages) else 'generating'
        return {**bundle,'status':status,'pages':pages,'complete':sum(p['status']=='ready' for p in pages),'total':len(pages)}

    def tick(self):
        if self.app.stop.is_set() or self.app.workflows._stop.is_set() or not self.lock.acquire(False):return False
        try:
            for row in self.db.rows("SELECT v.*,s.mode FROM repurpose_variants v JOIN platform_visual_sets s ON s.variant_id=v.id WHERE v.status='review' AND s.version!=v.version"):
                c=json.loads(row['content'])
                if row['platform']=='xiaohongshu':pages=[{'title':p['title'],'text':p['text'],'prompt':p['visual'],'slot':'xhs'} for p in c['pages']]
                else:
                    excerpt=plain(c['summary'] or c['body'])[:260]
                    pages=[{'title':c['title'],'text':plain(c['summary'])[:180],'prompt':'文章封面：'+c['title'],'slot':'cover'},
                           {'title':'Key ideas' if row['platform']=='devto' else '文章要点','text':excerpt,'prompt':'解释正文核心观点：'+excerpt,'slot':'body'}]
                with self.db.connect(immediate=True) as db:
                    current=db.execute('SELECT version FROM repurpose_variants WHERE id=?',(row['id'],)).fetchone()
                    if current['version']!=row['version']:continue
                    for i,p in enumerate(pages):db.execute("INSERT OR IGNORE INTO platform_visual_pages(id,variant_id,version,page_index,page,status) VALUES(?,?,?,?,?,'queued')",(uid(),row['id'],row['version'],i,dumps(p)))
                    db.execute("UPDATE platform_visual_sets SET version=?,status='generating',error='' WHERE variant_id=?",(row['version'],row['id']))
            row=self.db.one("SELECT p.*,v.platform,v.snapshot,s.mode FROM platform_visual_pages p JOIN repurpose_variants v ON v.id=p.variant_id AND v.version=p.version JOIN platform_visual_sets s ON s.variant_id=v.id WHERE p.status='queued' AND v.status='review' ORDER BY p.rowid LIMIT 1")
            if not row:return False
            token=uid()
            with self.db.connect(immediate=True) as db:db.execute("UPDATE platform_visual_pages SET status='running',token=? WHERE id=?",(token,row['id']))
            try:
                page=json.loads(row['page']);total=self.db.one('SELECT count(*) n FROM platform_visual_pages WHERE variant_id=? AND version=?',(row['variant_id'],row['version']))['n']
                if row['mode']=='cards':
                    raw=render_card(page,row['platform'],row['page_index'],total)
                else:
                    from . import image_provider
                    task='cover' if page['slot']=='cover' or row['page_index']==0 else 'illustration'
                    config=image_provider.configuration(self.app.config,task)
                    if not config['IMAGE_BASE_URL'] or not config['IMAGE_MODEL']:
                        fail('AI 图片模型尚未配置，请在模型与能力中配置并测试图片接口，再重试。文字稿已保留，不会改用排版图卡。')
                    context=json.loads(row['snapshot']);profile=context.get('profile') or {}
                    from .illustrations import reference_urls
                    refs=reference_urls(self.app,profile.get('visual_references',[]))
                    references=[(self.db.data_dir/'media'/Path(url).name).read_bytes() for url in refs]
                    prompt=(page['prompt']+'\n标题：'+page['title']+'\n需表达内容：'+page['text']+
                            '\n画像视觉风格：'+profile.get('visual_style','')+
                            '\n不虚构数据、终端输出，不添加二维码和无关标语。保留裁切安全区。'+
                            ('\n竖版知识图集，本页须独立清楚表达要点，中文可读。' if page['slot']=='xhs' else '\n为文章生成具体场景或解释插画。'))
                    page['model_route']={'task':task,'model':config['IMAGE_MODEL'],'base_url':config['IMAGE_BASE_URL'],
                                         'mode':'api','owner_kind':'platform_visual_pages','owner_id':row['id']}
                    page['reference_hashes']=[hashlib.sha256(raw).hexdigest() for raw in references]
                    with self.db.connect(immediate=True) as db:db.execute('UPDATE platform_visual_pages SET page=? WHERE id=? AND token=?',(dumps(page),row['id'],token))
                    with self.app.ai_lock:
                        from .model_provenance import tracked_call
                        raw=tracked_call(self.app,page['model_route'],
                            lambda:image_provider.generate(config,prompt,'1024x1536' if page['slot']=='xhs' else '1536x1024',references))
                    if page['slot']=='xhs':raw=image_provider.fit_portrait(raw)
                from .image_provider import image_bytes
                image_bytes(raw);asset=uid();url='/media/'+asset+('.png' if raw.startswith(b'\x89PNG') else '.jpg' if raw.startswith(b'\xff\xd8') else '.webp')
                with self.db.connect(immediate=True) as db:
                    active=db.execute("SELECT 1 FROM platform_visual_pages WHERE id=? AND status='running' AND token=?",(row['id'],token)).fetchone()
                    if not active:return True
                    current=db.execute('SELECT version,status FROM repurpose_variants WHERE id=?',(row['variant_id'],)).fetchone()
                    if current['version']!=row['version'] or current['status']!='review':
                        db.execute("UPDATE platform_visual_pages SET status='outdated',token='' WHERE id=?",(row['id'],));return True
                    (self.db.data_dir/'media').mkdir(exist_ok=True);(self.db.data_dir/'media'/Path(url).name).write_bytes(raw)
                    db.execute('INSERT INTO assets(id,url,title,collection,origin,created_at) VALUES(?,?,?,?,?,?)',(asset,url,page['title'],'平台图文待审','text-card' if row['mode']=='cards' else 'generated',now()))
                    db.execute("UPDATE platform_visual_pages SET status='ready',url=?,token='' WHERE id=? AND token=?",(url,row['id'],token))
            except Exception as exc:
                from .service import Problem
                from .adapters import DeliveryError
                error=str(exc) if isinstance(exc,(Problem,DeliveryError)) else '图片生成未完成，请重试；文字稿保留。'
                with self.db.connect(immediate=True) as db:db.execute("UPDATE platform_visual_pages SET status='failed',error=?,token='' WHERE id=? AND token=?",(error,row['id'],token))
            return True
        finally:self.lock.release()
