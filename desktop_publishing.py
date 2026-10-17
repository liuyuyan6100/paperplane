"""PaperPlane-owned platform windows. Cookies remain in WebView2 storage.

Draft writes and read-only publication checks; no Python API is exposed to remote pages.
Platform HTTP contracts are implemented in web/platform_transport.js.
ImageX temporary grants are used by the native uploader in memory only.
"""
import base64
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

from desktop_xhs import DesktopXhsBrowser
from studio.adapters import DeliveryError

PLATFORMS = {'csdn': ('CSDN', 'https://editor.csdn.net/md/'),
             'juejin': ('掘金', 'https://juejin.cn/editor/drafts/new?v=2'),
             'zhihu': ('知乎', 'https://zhuanlan.zhihu.com/write'),
             'cnblogs': ('博客园', 'https://i.cnblogs.com/posts/edit'),
             'oschina': ('开源中国', 'https://my.oschina.net/'),
             'segmentfault': ('思否', 'https://segmentfault.com/write'),
             '51cto': ('51CTO', 'https://blog.51cto.com/blogger/publish'),
             'toutiao': ('头条号', 'https://mp.toutiao.com/profile_v4/graphic/publish'),
             'bilibili-article': ('B站专栏', 'https://member.bilibili.com/platform/upload/text'),
             'jianshu': ('简书', 'https://www.jianshu.com/writer'),
             'weibo': ('微博', 'https://card.weibo.com/article/v5/editor'),
             'devto': ('DEV', 'https://dev.to/new')}
LOGIN_SCRIPT = (Path(__file__).parent / 'web' / 'platform_login_transport.js').read_text(encoding='utf-8')
SCRIPT = (Path(__file__).parent / 'web' / 'platform_transport.js').read_text(encoding='utf-8')
ZHIHU_SCRIPT = (Path(__file__).parent / 'web' / 'zhihu_transport.js').read_text(encoding='utf-8-sig')
CNBLOGS_SCRIPT = (Path(__file__).parent / 'web' / 'cnblogs_transport.js').read_text(encoding='utf-8')
OSCHINA_SCRIPT = (Path(__file__).parent / 'web' / 'oschina_transport.js').read_text(encoding='utf-8')
SEGMENTFAULT_SCRIPT = (Path(__file__).parent / 'web' / 'segmentfault_transport.js').read_text(encoding='utf-8')
CTO51_SCRIPT = (Path(__file__).parent / 'web' / 'cto51_transport.js').read_text(encoding='utf-8')
EXTRA_SCRIPTS = {platform: (Path(__file__).parent / 'web' / filename).read_text(encoding='utf-8-sig')
                 for platform, filename in {'toutiao':'toutiao_transport.js',
                     'bilibili-article':'bilibili_transport.js','jianshu':'jianshu_transport.js',
                     'weibo':'weibo_transport.js','devto':'devto_transport.js'}.items()}


class PlatformWindow(DesktopXhsBrowser):
    def __init__(self, platform, ready):
        super().__init__()
        self.platform = platform
        self.name, self.home = PLATFORMS[platform]
        self.ready = ready
        self.upload_diagnostics = {}
        self.account_state = 'unknown'
        self.account_changed = lambda: None
        self.identity_window = None
        self.image_window = None

    def _check(self):
        if self.stopped.is_set() or self.closed.is_set() or not self.window:
            raise DeliveryError(self.name + '窗口已关闭，请在纸飞机中重新打开。')
        current = urlsplit(self.window.get_current_url() or '')
        allowed = {urlsplit(self.home).netloc}
        if self.platform == 'oschina':
            allowed.add('www.oschina.net')
        if self.platform == 'weibo':
            allowed.add('weibo.com')
        if current.scheme != 'https' or current.netloc not in allowed:
            raise DeliveryError('请在纸飞机内置窗口完成' + self.name + '登录。')

    def _wait(self, *args, **kwargs):
        try:
            return super()._wait(*args, **kwargs)
        except DeliveryError as exc:
            raise DeliveryError(str(exc).replace('小红书', self.name), uncertain=exc.uncertain) from None

    def _ensure_window(self, hidden=False):
        if not self.ready.wait(45) or self.stopped.is_set():
            raise DeliveryError('纸飞机窗口尚未准备好。')
        if self.window:
            if not hidden:self.window.show()
            return
        import webview
        self.closed.clear()
        self.window = webview.create_window('纸飞机 · ' + self.name + '账号与草稿', self.home,
                                            width=1280, height=900, min_size=(1000, 700), hidden=hidden)
        def closed():
            self.closed.set()
            self.window = None
            self.account_state = 'remembered' if self.account else 'unknown'
        self.window.events.closed += closed
        if not self.window.events.loaded.wait(40):
            raise DeliveryError(self.name + '页面加载超时。')

    def call(self, action, arg=None):
        if self.platform == 'cnblogs' and action in ('identity', 'diagnose') and self.home.startswith('https://i.cnblogs.com/'):
            # The identity endpoint lives on a different origin. Query it in a
            # cookie-sharing page instead of relying on extension CORS privileges.
            if self.identity_window is None:
                self.identity_window = PlatformWindow('cnblogs', self.ready)
                self.identity_window.home = 'https://home.cnblogs.com/'
            self.identity_window._ensure_window(hidden=True)
            current = urlsplit(self.identity_window.window.get_current_url() or '')
            if current.scheme != 'https' or current.netloc != 'home.cnblogs.com':
                # A previous signed-out check may still be parked at the login page.
                # Only refresh the dedicated hidden page, never the user's editor.
                self.identity_window._navigate(self.identity_window.home)
            try:
                return self.identity_window.call(action, arg)
            finally:
                info=self.identity_window.upload_diagnostics
                if isinstance(info, dict) and isinstance(info.get('transport'), dict):
                    self.upload_diagnostics['transport']=dict(info['transport'])
        self._check()
        script = {'zhihu': ZHIHU_SCRIPT, 'cnblogs': CNBLOGS_SCRIPT, 'oschina': OSCHINA_SCRIPT,
                  'segmentfault': SEGMENTFAULT_SCRIPT, '51cto': CTO51_SCRIPT}.get(self.platform, SCRIPT)
        script = EXTRA_SCRIPTS.get(self.platform, script)
        expression = '(' + script + ')(' + ','.join(json.dumps(x, ensure_ascii=True)
                                                   for x in (self.platform, action, arg)) + ')'
        result = self._cdp('Runtime.evaluate', {'expression': expression, 'awaitPromise': True,
                                               'returnByValue': True, 'timeout': 170000}, timeout=175)
        if result.get('exceptionDetails'):
            raise DeliveryError(self.name + '请求未完成，请检查登录、验证提示或平台服务状态。')
        value = result.get('result', {}).get('value')
        if isinstance(value, dict) and isinstance(value.get('diagnostic'), dict):
            # Transport scripts expose only bounded protocol metadata, never response bodies or credentials.
            self.upload_diagnostics['transport'] = {'action': action, **value['diagnostic']}
        if not isinstance(value, dict) or value.get('ok') is not True:
            error = DeliveryError(self.name + '操作未完成：' + str((value or {}).get('error', '未收到有效响应'))[:240])
            error.submission_not_started = (action == 'save' and isinstance(value, dict)
                and value.get('uncertain') is False and isinstance(value.get('diagnostic'), dict)
                and value['diagnostic'].get('submission') == 'not_started')
            raise error
        return value.get('data')

    def login(self, progress):
        self._ensure_window()
        progress('正在核验已保存的' + self.name + '登录状态。')
        def identity():
            try:
                account = self.call('identity')
                if account and self.platform in ('weibo', 'toutiao'):
                    current = urlsplit(self.window.get_current_url() or '')
                    needs_editor = (self.platform == 'weibo' and current.netloc != urlsplit(self.home).netloc
                                    or self.platform == 'toutiao' and current.path not in ('/profile_v4/graphic/publish','/profile_v3/graphic/publish'))
                    if needs_editor:
                        self._navigate(self.home)
                        account = self.call('identity')
            except DeliveryError:
                self.account_state = 'unverified'
                return None
            self.account = account
            self.account_state = 'verified' if account else 'signed-out'
            self.account_changed()
            return account
        account = identity()
        if account:
            progress('已复用' + self.name + '登录：' + account['name'])
            return account
        progress('请在纸飞机内置' + self.name + '窗口检查登录或网络状态，完成后自动继续。')
        return self._wait(identity, self.name + '尚未登录，请登录后重试。', seconds=300)


class DesktopPublishingBrowser:
    def __init__(self, account_file=None):
        self.ready = threading.Event()
        self.account_file = Path(account_file) if account_file else None
        self.account_lock = threading.Lock()
        self.windows = {p: PlatformWindow(p, self.ready) for p in PLATFORMS}
        self.check_windows = {p: PlatformWindow(p, self.ready) for p in PLATFORMS}
        try:
            saved=json.loads(self.account_file.read_text(encoding='utf-8')) if self.account_file else {}
            for platform,window in self.windows.items():
                account=saved.get(platform)
                if isinstance(account,dict) and all(isinstance(account.get(k),str) and 0<len(account[k])<=200 for k in ('id','name')):
                    window.account={k:account[k] for k in ('id','name')}
                    window.account_state='remembered'
        except (OSError,ValueError,AttributeError):pass
        for window in self.windows.values():window.account_changed=self._remember_accounts

    def _remember_accounts(self):
        if not self.account_file:return
        with self.account_lock:
            try:
                self.account_file.parent.mkdir(parents=True,exist_ok=True)
                temp=self.account_file.with_suffix('.tmp')
                temp.write_text(json.dumps(self.accounts(),ensure_ascii=False),encoding='utf-8')
                temp.replace(self.account_file)
            except OSError:pass  # WebView2 owns the actual login session independently.

    def account_states(self):
        return {p:w.account_state for p,w in self.windows.items()}

    def accounts(self):
        return {p: w.account for p, w in self.windows.items()}

    def diagnostics(self):
        return {p:dict(w.upload_diagnostics) for p,w in self.windows.items()}

    def login(self, platform, progress):
        return self.windows[platform].login(progress)

    def upload(self, platform, path):
        if platform == 'zhihu':
            from studio.zhihu_images import upload
            return upload(self.windows[platform], path)
        if platform == 'juejin':
            from studio.juejin_images import upload
            return upload(self.windows[platform], path)
        window = self.windows[platform]
        payload = {'base64': base64.b64encode(path.read_bytes()).decode(),
                   'extension': path.suffix[1:].lower(),
                   'mime': 'image/' + ('jpeg' if path.suffix.lower() in ('.jpg','.jpeg') else path.suffix[1:].lower())}
        if platform == 'devto':
            payload['account_id'] = (window.account or {}).get('id')
        if platform == 'weibo':
            account = window.call('identity')
            if not account or account != window.account:
                raise DeliveryError('微博账号已变化，图片和正文尚未发送。')
            if window.image_window is None:
                window.image_window = PlatformWindow('weibo', self.ready)
                window.image_window.home = 'https://picupload.weibo.com/interface/pic_upload.php?app=miniblog&s=json&p=1&data=1'
            window.image_window._ensure_window(hidden=True)
            # Same-origin browser upload. Session cookies never leave WebView2.
            result = window.image_window.call('upload_binary', {**payload, 'account_id':account['id']})
            if window.call('identity') != account:
                raise DeliveryError('微博上传期间账号变化，正文尚未发送。')
            return window.call('upload_receipt', {'account_id':account['id'], 'result':result})
        return window.call('upload', payload)

    def save(self, platform, article, account):
        window = self.windows[platform]
        if window.call('identity') != account:
            raise DeliveryError('平台账号已变化，正文尚未发送，请重新核对账号。')
        try:
            return window.call('save', {**article, 'account_id': account['id']})
        except DeliveryError as exc:
            if getattr(exc, 'submission_not_started', False):
                raise DeliveryError('正文尚未提交。' + str(exc)[:240]) from None
            raise DeliveryError('草稿提交结果待核对，请打开平台草稿箱检查；不会自动重发。' + str(exc)[:240], uncertain=True) from None
        except Exception:
            raise DeliveryError('草稿提交结果待核对，请打开平台草稿箱检查；不会自动重发。', uncertain=True) from None

    def read_draft(self, platform, draft_id, article):
        # A separate page cannot overwrite an editor containing unsaved changes.
        window = self.check_windows[platform]
        window._ensure_window(hidden=True)
        account = window.call('identity')
        if not account or account.get('id') != article.get('account_id'):
            raise DeliveryError('当前平台账号与原任务不同，请切回送稿账号。')
        result = window.call('draft-read', {**article, 'draft_id': draft_id})
        if window.call('identity') != account:
            raise DeliveryError('核对期间账号变化，保留原任务状态。')
        self.windows[platform].account = account
        self.windows[platform].account_state = 'verified'
        self.windows[platform].account_changed()
        return result

    def open(self, platform, url):
        from studio.publishing_bridge import valid_url
        if not valid_url(url, platform):
            raise DeliveryError('草稿地址不属于目标平台。')
        window = self.windows[platform]
        window._ensure_window()
        window.window.load_url(url)

    def check_publication(self, platform, draft_id, expected_account=None):
        """Read server evidence without showing or navigating the user's editor."""
        window = self.windows[platform]
        if window.window and urlsplit(window.window.get_current_url() or '').netloc != urlsplit(window.home).netloc:
            # A publish-success redirect may leave the editor origin. Use a
            # separate hidden page sharing WebView2 cookies; never navigate it back.
            window = self.check_windows[platform]
        window._ensure_window(hidden=True)
        account = window.call('identity')
        if not account or not account.get('id'):
            window.account_state = 'signed-out'
            raise DeliveryError('平台登录已失效，请在纸飞机内重新登录后核对。')
        if expected_account and account['id'] != expected_account:
            raise DeliveryError('当前平台账号与送稿账号不同，请切回原账号后核对。')
        self.windows[platform].account = account
        self.windows[platform].account_state = 'verified'
        self.windows[platform].account_changed()
        result = window.call('publication', {'draft_id':str(draft_id),'account_id':account['id']})
        if window.call('identity') != account:
            raise DeliveryError('核对期间平台账号变化，保留原发表状态。')
        return result

    def notify(self, platform, message):
        window=self.windows[platform]
        if not window.window:return
        window.window.evaluate_js('''(()=>{
          let box=document.getElementById('paperplane-delivery-status');
          if(!box){box=document.createElement('div');box.id='paperplane-delivery-status';document.body.append(box);}
          box.setAttribute('role','status');box.textContent='纸飞机：'+MESSAGE;
          box.style.cssText='position:fixed;bottom:32px;left:20px;right:20px;z-index:2147483647;padding:14px 18px;background:#fff4d6;color:#513b0a;border:1px solid #e5c779;border-radius:8px;font:14px/1.6 system-ui;box-shadow:0 3px 16px #0002';
        })()'''.replace('MESSAGE',json.dumps(message,ensure_ascii=True)))

    def stop(self):
        for window in [*self.windows.values(), *self.check_windows.values()]:
            if window.identity_window:
                window.identity_window.stop()
            if window.image_window:
                window.image_window.stop()
            window.stop()
