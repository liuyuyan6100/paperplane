"""Local Wechatsync extension protocol adapter. No CLI, agent or cloud relay."""
import asyncio
import base64
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from .adapters import DeliveryError
from .db import uid
from .markdown import render
from .platform_fields import delivery_summary

from .platform_catalog import BRIDGE_PLATFORMS as PLATFORMS, LOGIN_ONLY_PLATFORMS, DESKTOP_LOGIN_PLATFORMS
ORIGIN='chrome-extension://hchobocdmclopcbnibdnoafilagadion'

class PublishingBridge:
    def __init__(self,app,port=9527):
        self.port=port
        self.app=app;self.loop=None;self.server=None;self.client=None;self.pending={}
        self.thread=None;self.error='';self.ready=threading.Event();self.serial=threading.Lock()
        self.browser=None;self.busy=False;self.progress='';self.desktop_error=''

    def status(self):
        return {'enabled':self.browser is not None or self.app.config.get('PUBLISH_BRIDGE_ENABLED',False) is True,
                'connected':self.client is not None,'address':f'ws://127.0.0.1:{self.port}',
                'error':self.desktop_error if self.browser else self.error,'platforms':sorted(PLATFORMS),'mode':'draft',
                'desktop':self.browser is not None,'accounts':self.browser.accounts() if self.browser else {},
                'account_states':self.browser.account_states() if self.browser else {},
                'busy':self.busy,'progress':self.progress,
                'upload_diagnostics':self.browser.diagnostics() if self.browser else {}}

    def fingerprint(self, platform):
        import hashlib
        account = self.browser.accounts().get(platform) if self.browser else None
        return hashlib.sha256(json.dumps(['desktop-publishing',str(self.app.db.data_dir.resolve()),
                                          platform,(account or {}).get('id')]).encode()).hexdigest()

    def _background_checking(self):
        sync=getattr(self.app, 'publication_sync', None)
        if getattr(sync, 'checking', False) is True:return True
        # The reader may be between acquiring/releasing serial and updating its
        # flag. If no foreground operation owns busy, wait instead of failing a
        # delivery during that handoff. Only an active poller enables this path.
        thread=getattr(sync, 'thread', None)
        return bool(not self.busy and thread and thread.is_alive())

    def _wait_for_serial(self):
        deadline = time.monotonic() + 240
        while not self.app.stop.is_set() and time.monotonic() < deadline:
            if self.serial.acquire(timeout=.2):return True
        return False

    def launch(self, platform, job_id=None):
        from .service import Problem
        if platform not in DESKTOP_LOGIN_PLATFORMS or not self.browser:
            raise Problem('请使用 Windows 安装版内置平台窗口。',409)
        job = self.app.job_detail(job_id) if job_id else None
        if job and (job['channel_id'] != platform or job['deleted_at'] or
                    job['status'] not in ('draft','uncertain') or not valid_url(job.get('url'),platform)):
            raise Problem('此记录没有可打开的平台草稿地址。',409)
        if self.busy:raise Problem('平台窗口正在处理任务，请稍候。',409)
        acquired=self.serial.acquire(blocking=False)
        if not acquired and not self._background_checking():raise Problem('平台窗口正在处理任务，请稍候。',409)
        self.busy=True;self.desktop_error=''
        def run():
            locked=acquired
            try:
                if not locked:
                    self.progress='正在完成后台核对，随后打开平台窗口。'
                    locked=self._wait_for_serial()
                    if not locked:raise DeliveryError('平台窗口等待已停止，请稍后重新打开。')
                if job:self.browser.open(platform,job['url'])
                else:self.browser.login(platform,lambda text:setattr(self,'progress',text))
                self.progress=('已打开平台草稿。' if job else 'DEV图片上传账号已核验；发送时仍会比对API Key所属账号。' if platform=='devto' else '平台账号已核验；自动送稿尚未接通。' if platform in LOGIN_ONLY_PLATFORMS else '平台已登录，可以从纸飞机送稿。')
            except Exception as exc:
                self.desktop_error=str(exc) if isinstance(exc,DeliveryError) else '平台窗口未能打开，请重试。'
            finally:
                self.busy=False
                if locked:self.serial.release()
        threading.Thread(target=run,daemon=True,name='publishing-login').start()
        return self.status()

    def send_desktop(self, post, platform, job_id=None):
        if platform not in PLATFORMS or not self.browser:raise DeliveryError('此任务需要纸飞机 Windows 内置平台窗口。')
        acquired=self.serial.acquire(blocking=False)
        if not acquired and self._background_checking():
            self.progress='等待后台核对完成，随后继续送稿。'
            acquired=self._wait_for_serial()
        if not acquired:raise DeliveryError('平台窗口正在处理另一项操作，请完成后重试。')
        self.busy=True;self.desktop_error=''
        try:
            account=self.browser.login(platform,lambda text:setattr(self,'progress',text))
            self._notice(platform,'正在准备送稿。当前是平台登录页，尚未生成本篇草稿。')
            expected=post.get('delivery_config',{}).get('account_id')
            if expected and account['id']!=expected:raise DeliveryError('平台账号已变化，请切回安排任务时的账号。')
            body=post['body'];cover=post.get('cover','')
            urls=set(re.findall(r'/media/[a-f0-9]{32}\.(?:png|jpg|gif|webp)',body))
            if cover.startswith('/media/'):urls.add(cover)
            root=(self.app.db.data_dir/'media').resolve()
            paths={}
            for url in urls:
                path=(root/Path(url).name).resolve()
                if not re.fullmatch(r'/media/[a-f0-9]{32}\.(png|jpg|gif|webp)',url) or path.parent!=root or not path.is_file() or not 0<path.stat().st_size<=5_000_000:
                    raise DeliveryError('图片缺失、路径无效或超过5MB，正文尚未发送。')
                paths[url]=path
            for index,(url,path) in enumerate(sorted(paths.items()),1):
                self.progress=f'正在上传图片 {index}/{len(paths)}'
                self._notice(platform,self.progress+'，图片完成后才提交正文。')
                remote=self.browser.upload(platform,path)
                if not valid_url(remote):raise DeliveryError('图片上传没有返回有效地址，正文尚未发送。')
                body=body.replace(url,remote)
                if cover==url:cover=remote
            if '/media/' in body or '/media/' in cover:raise DeliveryError('存在未上传图片，正文尚未发送。')
            self.progress='图片已上传，正在提交平台草稿。'
            article={'title':post['title'],'markdown':body,'content':render(body),
                     'cover':cover,'summary':delivery_summary(platform,post.get('summary','')),
                     'tags':post.get('tags',[]),'account_id':account['id']}
            # Persist the exact post-upload body before the request can create a draft.
            # A lost response can then be reconciled without uploading or sending again.
            if job_id:
                from .db import dumps
                with self.app.db.connect(immediate=True) as db:
                    row=db.execute('SELECT snapshot,status FROM jobs WHERE id=?',(job_id,)).fetchone()
                    if not row or row['status']!='running':
                        raise DeliveryError('送稿任务状态已变化，正文尚未发送。')
                    post['desktop_submission']=article
                    db.execute('UPDATE jobs SET snapshot=? WHERE id=?',(dumps(post),job_id))
            result=self.browser.save(platform,article,account)
            if not isinstance(result,dict) or not re.fullmatch(r'\d+',str(result.get('id',''))) or not valid_url(result.get('url'),platform):
                raise DeliveryError('未收到有效草稿回执，请核对平台草稿箱；不会自动重发。',uncertain=True)
            expected_urls = {
                'toutiao': f"https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id={result['id']}",
                'bilibili-article': f"https://member.bilibili.com/platform/upload/text/edit?aid={result['id']}",
                'weibo': f"https://card.weibo.com/article/v5/editor#/draft/{result['id']}",
            }
            if platform in expected_urls and result['url'] != expected_urls[platform]:
                raise DeliveryError('草稿地址与返回编号不一致，请核对平台草稿箱。',uncertain=True)
            if platform == 'jianshu' and not result['url'].endswith('/notes/'+str(result['id'])):
                raise DeliveryError('简书草稿地址与返回编号不一致，请核对平台草稿箱。',uncertain=True)
            incomplete=result.get('incomplete') is True
            if platform == '51cto' and result['url'] != f"https://blog.51cto.com/blogger/draft/{result['id']}":
                raise DeliveryError('51CTO草稿地址与编号不一致，请核对草稿箱。',uncertain=True)
            if platform == 'segmentfault' and result['url'] != f"https://segmentfault.com/write?draftId={result['id']}":
                raise DeliveryError('思否草稿地址与编号不一致，请核对草稿箱。',uncertain=True)
            if platform == 'oschina' and result['url'] != f"https://my.oschina.net/u/{account['id']}/blog/ai-write/draft/{result['id']}":
                raise DeliveryError('开源中国草稿地址与账号或编号不一致，请核对草稿箱。',uncertain=True)
            self.progress=('平台已建立草稿，但正文或封面保存未能确认；请打开原草稿核对，不会自动重发。' if incomplete else '已送入平台草稿箱，可在纸飞机中打开核对。')
            receipt={'draft_id':str(result['id']),'draft_url':result['url'],'account_id':account['id']}
            if incomplete and platform == 'zhihu':
                # The Zhihu transport emits only bounded, safe diagnostics; signed image
                # references stay in this job's submission, never in this receipt.
                if isinstance(result.get('reason'),str):
                    self.progress+=' '+result['reason'][:240]
                if isinstance(result.get('observed'),dict):
                    receipt['draft_readback']=result['observed']
            try:self.browser.open(platform,result['url'])
            except Exception:
                if not incomplete:self.progress='草稿已保存，窗口打开失败；请从发布记录打开草稿。'
            return {'status':'uncertain' if incomplete else 'draft','url':result['url'],'external_id':str(result['id']),
                    'note':self.progress+' 尚未公开发表。','fingerprint':self.fingerprint(platform),
                    'desktop_receipt':receipt}
        except DeliveryError as exc:
            self.desktop_error=str(exc)
            self.progress=('提交结果待核对，请检查平台草稿箱。' if exc.uncertain else '送稿失败，尚未生成本篇草稿。')
            self._notice(platform,self.progress+' '+str(exc)+' 请返回纸飞机查看任务详情。')
            raise
        finally:self.busy=False;self.serial.release()

    def _notice(self, platform, message):
        try:self.browser.notify(platform,message)
        except Exception:pass  # A display problem must never change delivery outcome.

    def setup(self):
        if not self.app.config.get('PUBLISH_BRIDGE_TOKEN'):
            self.app.config.update({'PUBLISH_BRIDGE_TOKEN':secrets.token_urlsafe(32)})
        self.app.config.update({'PUBLISH_BRIDGE_ENABLED':True})
        self.start()
        return {**self.status(),'token':self.app.config.get('PUBLISH_BRIDGE_TOKEN')}

    def start(self):
        if self.browser or not self.status()['enabled'] or self.thread and self.thread.is_alive():return
        self.ready.clear()
        self.thread=threading.Thread(target=self._run,daemon=True,name='publishing-bridge');self.thread.start()
        self.ready.wait(3)

    def _run(self):
        self.loop=asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        async def start():
            from websockets.asyncio.server import serve
            self.server=await serve(self._client,'127.0.0.1',self.port,origins=[ORIGIN],max_size=10_000_000)
            self.port=self.server.sockets[0].getsockname()[1]
        try:
            self.loop.run_until_complete(start());self.error='';self.ready.set();self.loop.run_forever()
        except Exception:
            self.error='本地发布连接未启动：请检查9527端口是否被其他同步助手占用。';self.ready.set()
        finally:
            if self.server:
                self.server.close();self.loop.run_until_complete(self.server.wait_closed())
            self.loop.close();self.client=None

    async def _client(self,ws):
        if self.client is not None:
            await ws.close(code=1008,reason='Already connected');return
        self.client=ws
        try:
            async for raw in ws:
                try:message=json.loads(raw)
                except (ValueError,TypeError):continue
                if not isinstance(message,dict):continue
                future=self.pending.get(message.get('id'))
                if future and not future.done():future.set_result(message)
        finally:
            self.client=None
            for future in list(self.pending.values()):
                if not future.done():future.set_exception(ConnectionError())

    async def _request(self,method,params,timeout):
        if self.client is None:raise ConnectionError()
        ident=uid();future=self.loop.create_future();self.pending[ident]=future
        try:
            await self.client.send(json.dumps({'id':ident,'method':method,'token':self.app.config.get('PUBLISH_BRIDGE_TOKEN'),'params':params}))
            return await asyncio.wait_for(future,timeout)
        finally:self.pending.pop(ident,None)

    def request(self,method,params,timeout=60,commit=False):
        if self.client is None or not self.loop:raise DeliveryError('浏览器同步扩展未连接，请打开渠道设置完成连接。')
        future=asyncio.run_coroutine_threadsafe(self._request(method,params,timeout),self.loop)
        try:reply=future.result(timeout+2)
        except Exception:
            future.cancel()
            raise DeliveryError('发布连接中断或超时；请先核对平台草稿箱，不要重复发送。' if commit else '浏览器连接中断，请重新连接后再试。',uncertain=commit) from None
        if reply.get('error'):
            raise DeliveryError('同步扩展拒绝请求，请检查扩展令牌、平台登录及权限。',uncertain=commit)
        return reply.get('result')

    def accounts(self):
        with self.serial:result=self.request('listPlatforms',{'forceRefresh':True},30)
        if not isinstance(result,list):raise DeliveryError('扩展未返回有效的平台账号列表')
        return [{'id':r.get('id'),'name':str(r.get('name',''))[:80],'authenticated':r.get('isAuthenticated') is True,
                 'username':str(r.get('username',''))[:100]} for r in result if isinstance(r,dict) and r.get('id') in PLATFORMS]

    def upload(self,url,platform):
        if not re.fullmatch(r'/media/[a-f0-9]{32}\.(png|jpg|gif|webp)',url):raise DeliveryError('本地图片路径无效')
        path=self.app.db.data_dir/'media'/Path(url).name
        if not path.is_file() or path.stat().st_size>5_000_000:raise DeliveryError('本地图片不存在或超过5MB')
        encoded=base64.b64encode(path.read_bytes()).decode();mime='image/'+('jpeg' if path.suffix=='.jpg' else path.suffix[1:])
        if len(encoded)<1024*1024:
            result=self.request('uploadImage',{'imageData':encoded,'mimeType':mime,'platform':platform})
        else:
            ident=uid();chunks=[encoded[i:i+512*1024] for i in range(0,len(encoded),512*1024)]
            self.request('uploadImage:start',{'uploadId':ident,'totalChunks':len(chunks),'mimeType':mime,'platform':platform})
            for i,chunk in enumerate(chunks):self.request('uploadImage:chunk',{'uploadId':ident,'chunkIndex':i,'data':chunk})
            result=self.request('uploadImage:complete',{'uploadId':ident})
        remote=result.get('url','') if isinstance(result,dict) else ''
        if not valid_url(remote):raise DeliveryError('图片上传未返回有效网址，正文尚未发送。')
        return remote

    def send(self,post,platform):
        if platform not in PLATFORMS:raise DeliveryError('此平台未接入自动送草稿')
        with self.serial:
            accounts=self.request('listPlatforms',{'forceRefresh':True},30)
            if not isinstance(accounts,list) or not any(isinstance(r,dict) and r.get('id')==platform and r.get('isAuthenticated') is True for r in accounts):
                raise DeliveryError('目标平台未登录，请在浏览器登录后重试；尚未发送。')
            body=post['body'];cover=post.get('cover','')
            local=set(re.findall(r'/media/[a-f0-9]{32}\.(?:png|jpg|gif|webp)',body))
            if cover.startswith('/media/'):local.add(cover)
            for url in sorted(local):
                remote=self.upload(url,platform);body=body.replace(url,remote)
                if cover==url:cover=remote
            if '/media/' in body or cover.startswith('/media/'):raise DeliveryError('存在未上传的本地图片，未发送正文。')
            result=self.request('syncArticle',{'platforms':[platform],'article':{'title':post['title'],'markdown':body,'content':render(body),'cover':cover}},120,commit=True)
            rows=result.get('results',[]) if isinstance(result,dict) else []
            matches=[r for r in rows if isinstance(r,dict) and r.get('platform')==platform]
            if len(matches)!=1 or matches[0].get('success') is not True:
                raise DeliveryError('未收到目标平台的成功回执，请核对草稿箱后再决定是否重试。',uncertain=True)
            row=matches[0];url=row.get('postUrl','')
            if not valid_url(url,platform):raise DeliveryError('回执缺少有效的平台草稿地址，请核对草稿箱。',uncertain=True)
            return {'status':'draft','url':url,'external_id':str(row.get('postId',''))[:200],
                    'note':'已自动送入平台草稿箱，尚未公开发表。请打开平台草稿核对。'}

    def close(self):
        if self.browser:self.browser.stop()
        if self.loop and self.loop.is_running():self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:self.thread.join(timeout=4)

def valid_url(url,platform=None):
    try:
        p=urlsplit(url)
        if p.scheme!='https' or not p.hostname or p.username or p.password:return False
        if platform == 'toutiao':
            return p.hostname == 'mp.toutiao.com' and not p.port and not p.fragment and p.path == '/profile_v4/graphic/publish' and re.fullmatch(r'pgc_id=[1-9][0-9]{0,39}',p.query) is not None
        if platform == 'bilibili-article':
            return p.hostname == 'member.bilibili.com' and not p.port and not p.fragment and p.path == '/platform/upload/text/edit' and re.fullmatch(r'aid=[1-9][0-9]{0,39}',p.query) is not None
        if platform == 'jianshu':
            return p.hostname == 'www.jianshu.com' and not p.port and not p.query and p.path == '/writer' and re.fullmatch(r'/notebooks/[1-9][0-9]{0,39}/notes/[1-9][0-9]{0,39}',p.fragment) is not None
        if platform == 'weibo':
            return p.hostname == 'card.weibo.com' and not p.port and not p.query and p.path == '/article/v5/editor' and re.fullmatch(r'/draft/[1-9][0-9]{0,39}',p.fragment) is not None
        if platform == '51cto':
            return p.hostname == 'blog.51cto.com' and not p.port and not p.query and not p.fragment and re.fullmatch(r'/blogger/draft/[1-9][0-9]{0,39}',p.path) is not None
        if platform == 'segmentfault':
            return p.hostname == 'segmentfault.com' and not p.port and not p.fragment and p.path == '/write' and re.fullmatch(r'draftId=[1-9][0-9]{0,39}', p.query) is not None
        if platform == 'oschina':
            return p.hostname == 'my.oschina.net' and not p.port and not p.query and not p.fragment and re.fullmatch(r'/u/[1-9][0-9]{0,15}/blog/ai-write/draft/[1-9][0-9]{0,15}', p.path) is not None
        if platform == 'cnblogs':
            return p.hostname == 'i.cnblogs.com' and not p.port and not p.query and not p.fragment and re.fullmatch(r'/articles/edit;postId=[1-9][0-9]{0,39}', p.path) is not None
        if platform == 'zhihu':
            return p.hostname == 'zhuanlan.zhihu.com' and not p.port and not p.query and not p.fragment and re.fullmatch(r'/p/[0-9]{1,40}(?:/edit)?', p.path) is not None
        if platform:
            domain={'csdn':'csdn.net','juejin':'juejin.cn','zhihu':'zhihu.com'}.get(platform)
            if not domain:return False
            return p.hostname==domain or p.hostname.endswith('.'+domain)
        return True
    except (ValueError,TypeError):return False
