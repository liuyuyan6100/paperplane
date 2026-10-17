"""Persistent WebView2 window used by PaperPlane's draft-only XHS adapter.

All UI operations are constrained to the creator origin. No remote debugging
port, extracted credentials or callable Python API is given to the website.
"""
import json
import re
import threading
import time
from urllib.parse import urlsplit

from studio.adapters import DeliveryError

HOME = 'https://creator.xiaohongshu.com/new/home'
EDITOR = 'https://creator.xiaohongshu.com/publish/publish?target=image'

# DOM selectors observed in the creator image editor on 2026-10-06.
SCRIPT = r"""
(function(action, arg) {
  if (location.origin !== 'https://creator.xiaohongshu.com') throw Error('小红书页面地址发生变化');
  const visible = e => {
    if(!e || e.getClientRects().length===0)return false;
    const style=getComputedStyle(e);
    return style.display!=='none' && style.visibility!=='hidden' && style.visibility!=='collapse' && style.opacity!=='0';
  };
  const unique = (selector) => {
    const found = [...document.querySelectorAll(selector)].filter(visible);
    if (found.length !== 1) throw Error('小红书页面控件不唯一或尚未加载：' + selector);
    return found[0];
  };
  const editorSelector='.tiptap.ProseMirror[contenteditable="true"], .ql-editor[contenteditable="true"]';
  const editorNodes=()=>[...document.querySelectorAll(editorSelector)];
  const visibleOne=selector=>{const nodes=[...document.querySelectorAll(selector)].filter(visible);return nodes.length===1?nodes[0]:null;};
  const editor = () => visibleOne(editorSelector);
  const title = () => visibleOne('input[placeholder*="标题"]');
  const text = e => (e?.innerText || '').replace(/\r\n/g,'\n').trim();
  const images = () => [...document.querySelectorAll('.img-preview-area img.preview')].filter(visible);
  const identity = () => {
    const m = (document.body?.innerText || '').match(/小红书账号\s*[:：]\s*([A-Za-z0-9_-]+)/);
    return m ? {id:m[1], name:text(document.querySelector('.name-box'))} : null;
  };
  if (action === 'identity') return identity();
  if (action === 'identity-state') {
    const inputs=[...document.querySelectorAll('input')].filter(visible);
    const phone=inputs.some(e=>/手机号|手机号码/.test(e.placeholder||''));
    const code=inputs.some(e=>/验证码/.test(e.placeholder||''));
    const loginButton=[...document.querySelectorAll('button,[role="button"]')].some(e=>visible(e)&&/^(?:登\s*录|登录\/注册|立即登录)$/.test(text(e)));
    const loginPage=/^\/login\/?$/.test(location.pathname) || (phone && code && loginButton);
    const loading=[...document.querySelectorAll('[aria-busy="true"],[role="progressbar"],[data-loading="true"]')].some(visible);
    return {account:identity(),ready:document.readyState==='complete',loginPage,loading,path:location.pathname};
  }
  if (action === 'state') return {title:title()?.value, caption:text(editor()),
    editing:editorNodes().some(visible), images:images().length, imagesReady:images().every(i=>i.complete && i.naturalWidth>0),
    upload:!!document.querySelector('input[type="file"]'),
    drawer:visible(document.querySelector('.draft-drawer')),
    imageTab:!!document.querySelector('.draft-tabs .tab-item.active')?.textContent.startsWith('图文笔记')};
  const buttonLabels=new Set(['暂存离开','保存草稿','保存','取消','确定','继续编辑','退出','返回','发布','确认发布']);
  const disabled=e=>!!e.disabled || e.matches(':disabled') || e.getAttribute('aria-disabled')==='true';
  const allButtons=()=>{
    const nodes=[...document.querySelectorAll('button,[role="button"]')];
    for(const host of document.querySelectorAll('xhs-publish-btn'))if(host.shadowRoot)nodes.push(...host.shadowRoot.querySelectorAll('button,[role="button"]'));
    return [...new Set(nodes)];
  };
  if(action==='save-state') {
    const nodes=editorNodes(),count=nodes.filter(visible).length;
    const dialogs=[...document.querySelectorAll('[role="dialog"],.d-dialog,.d-modal')].filter(visible);
    const buttons=allButtons().filter(e=>buttonLabels.has(text(e))).slice(0,30).map(e=>({label:text(e),visible:visible(e),disabled:disabled(e)}));
    const hosts=[...document.querySelectorAll('xhs-publish-btn')].slice(0,10).map(e=>({
      visible:visible(e),save_enabled:e.getAttribute('is-save-draft')==='true',
      save_disabled:e.getAttribute('save-disabled')==='true',
      save_disabled_known:['true','false'].includes(e.getAttribute('save-disabled')),
      draft_label:e.getAttribute('save-text')==='暂存离开',handler:typeof e._onSave==='function'}));
    const notices=[...document.querySelectorAll('[role="alert"],[role="status"],.d-message,.d-toast,.el-message,.ant-message-notice')].filter(visible);
    const messages={'保存成功':'saved','暂存成功':'saved','草稿保存成功':'saved','保存失败':'save_failed','暂存失败':'save_failed','图片上传中':'image_uploading','请稍后重试':'retry_later'};
    const signals=[...new Set(notices.map(e=>messages[text(e)]).filter(Boolean))];
    return {path:location.pathname,ready:document.readyState==='complete',editing:count>0,
      editor_total:nodes.length,editor_visible:count,title_visible:[...document.querySelectorAll('input[placeholder*="标题"]')].filter(visible).length,
      image_count:images().length,images_ready:images().every(i=>i.complete&&i.naturalWidth>0),
      drawer:visible(document.querySelector('.draft-drawer')),
      uploader_ready:count===0&&visible(document.querySelector('.draft-title-box'))&&!!document.querySelector('input[type="file"]'),
      home_ready:location.pathname==='/new/home'&&!!identity(),
      dialog_count:dialogs.length,buttons,save_hosts:hosts,signals};
  }
  if (action === 'drafts') return [...document.querySelectorAll('.draft-item[data-draft-type="image"]')].map(e=>({
    id:e.getAttribute('data-draft-id'), title:text(e.querySelector('.draft-title-text'))}));
  if (action === 'draft-state') {
    const drawer=document.querySelector('.draft-drawer');
    const open=visible(drawer);
    const active=drawer?.querySelector('.draft-tabs .tab-item.active') || document.querySelector('.draft-tabs .tab-item.active');
    const imageTab=open && text(active).startsWith('图文笔记');
    const countMatch=text(active).match(/^图文笔记\s*[（(]\s*(\d+)\s*[)）]\s*$/);
    const tabCount=countMatch ? Number(countMatch[1]) : null;
    const all=open ? [...drawer.querySelectorAll('.draft-item')].filter(visible) : [];
    const rows=all.filter(e=>e.getAttribute('data-draft-type')==='image');
    // An active tab is not a completed list request. Recognized loading signals
    // win over stale rows or an empty-state label rendered during a refresh.
    const loading=open && (drawer.getAttribute('aria-busy')==='true' ||
      [...drawer.querySelectorAll('[aria-busy="true"], [role="progressbar"], [data-loading="true"], [class*="loading"], [class*="skeleton"]')].some(visible));
    const emptyLabel=/^(?:暂无(?:图文(?:笔记)?)?草稿|还没有(?:图文)?草稿|这里还没有草稿|暂无内容)[。！!]?$/;
    const empty=open && all.length===0 && [...drawer.querySelectorAll('*')].some(e=>
      visible(e) && e.children.length===0 && emptyLabel.test(text(e)));
    return {origin:location.origin,path:location.pathname,drawer:open,imageTab,tabCount,loading,empty,
      rowCount:all.length,untypedCount:all.filter(e=>!e.getAttribute('data-draft-type')).length,
      drafts:rows.map(e=>({id:e.getAttribute('data-draft-id'),title:text(e.querySelector('.draft-title-text'))}))};
  }
  if (action === 'box') {unique('.draft-title-box').click(); return true;}
  if (action === 'image-tab') {
    const tabs=[...document.querySelectorAll('.draft-tabs .tab-item')].filter(e=>text(e).startsWith('图文笔记'));
    if(tabs.length!==1)throw Error('找不到图文草稿标签'); tabs[0].click();return true;
  }
  if(action === 'close-box'){unique('.draft-drawer .d-drawer-close').click();return true;}
  if(action === 'edit-draft') {
    const item=[...document.querySelectorAll('.draft-item[data-draft-type="image"]')].filter(e=>e.getAttribute('data-draft-id')===arg);
    if(item.length!==1)throw Error('找不到对应草稿，可能已发表、删除或浏览器数据已清理');
    const buttons=[...item[0].querySelectorAll('.draft-actions .btn')].filter(e=>text(e)==='编辑');
    if(buttons.length!==1)throw Error('草稿编辑入口已变化');buttons[0].click();return true;
  }
  if(action === 'focus-title') {
    const el=unique('input[placeholder*="标题"]');
    if(el.disabled || el.readOnly)throw Error('小红书标题输入框暂不可编辑');
    el.focus();el.select();
    if(document.activeElement!==el)throw Error('小红书标题输入框未取得焦点');
    return true;
  }
  if(action === 'focus-caption'){const el=editor();if(!el)throw Error('文案编辑器未加载');el.focus();return true;}
  if(action === 'save') {
    if(editorNodes().filter(visible).length!==1)throw Error('小红书当前可见编辑器不唯一，已停止保存');
    const buttons=allButtons().filter(e=>visible(e)&&text(e)==='暂存离开');
    if(buttons.length===1) {
      if(disabled(buttons[0]))throw Error('暂存草稿按钮尚不可用，请核对小红书窗口');
      buttons[0].click();return {method:'visible-button'};
    }
    if(buttons.length>1)throw Error('暂存草稿按钮不唯一，已停止自动操作');
    const hosts=[...document.querySelectorAll('xhs-publish-btn')].filter(visible);
    if(hosts.length===1) {
      const el=hosts[0];
      if(el.getAttribute('is-save-draft')==='true' && el.getAttribute('save-text')==='暂存离开'
        && el.getAttribute('save-disabled')==='false' && !disabled(el) && typeof el._onSave==='function') {
        el._onSave();return {method:'visible-component'};
      }
    }
    throw Error('无法识别可用的暂存草稿按钮，请在小红书窗口核对；不会点击发布');
  }
  throw Error('未知草稿操作');
})
"""


class DesktopXhsBrowser:
    def __init__(self):
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.stopped = threading.Event()
        self.window = None
        self.current_draft = None
        self.account = None
        self.diagnostics = {}

    def stop(self):
        self.stopped.set()
        if self.window:
            try: self.window.destroy()
            except Exception: pass

    def _check(self):
        if self.stopped.is_set() or self.closed.is_set():
            raise DeliveryError('小红书窗口已关闭，任务已停止，请核对草稿后继续。')
        if not self.window or urlsplit(self.window.get_current_url() or '').netloc != 'creator.xiaohongshu.com':
            raise DeliveryError('请在纸飞机的小红书窗口完成登录，不要切换到其他网站。')

    def _ensure_window(self):
        if not self.ready.wait(45) or self.stopped.is_set():
            raise DeliveryError('纸飞机窗口尚未准备好，请打开工作台后重试。')
        if self.window:
            self.window.show()
            return
        import webview
        self.closed.clear()
        self.window = webview.create_window('纸飞机 · 小红书登录与草稿', HOME, width=1280, height=900, min_size=(1000,700))
        def on_closed():
            self.closed.set()
            self.window = None
            self.current_draft = None
        self.window.events.closed += on_closed
        if not self.window.events.loaded.wait(40):
            raise DeliveryError('小红书页面加载超时，请检查窗口。')

    def _call(self, action, arg=None):
        self._check()
        try:
            return self.window.evaluate_js(SCRIPT + '(' + json.dumps(action) + ',' + json.dumps(arg, ensure_ascii=True) + ')')
        except Exception as exc:
            raise DeliveryError('小红书页面操作未完成：' + str(exc)[:250]) from None

    def _wait(self, fn, message, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stopped.is_set() or self.closed.is_set():
                raise DeliveryError('小红书窗口已关闭，请核对草稿后继续。')
            value = fn()
            if value: return value
            self.stopped.wait(min(.4, max(0, deadline - time.monotonic())))
        raise DeliveryError(message)

    def _wait_editor_settled(self, matches):
        # Official Title component debounces behavior.modifyTitle by 300ms.
        # An immediately matching DOM value is not yet the state saved by Vue.
        stable_since = None
        def settled():
            nonlocal stable_since
            if not matches():
                stable_since = None
                return False
            current = time.monotonic()
            if stable_since is None:
                stable_since = current
            return current - stable_since >= .8
        self._wait(settled, '小红书标题、文案或图片尚未稳定，已停止自动保存。')

    def _navigate(self, url):
        self._check()
        if self.window.get_current_url() == url:
            return
        self.window.events.loaded.clear()
        self.window.load_url(url)
        self._wait(lambda: self.window.events.loaded.is_set(), '小红书页面打开超时。')

    def _cdp(self, method, params, timeout=30):
        """Use the existing WebView2 object, without exposing a debugging port."""
        self._check()
        from System import Action, String
        from System.Threading.Tasks import Task
        webview = self.window.native.webview
        done, result = threading.Event(), {}
        def callback(task):
            try:
                result['value'] = json.loads(str(task.Result))
            except Exception:
                result['error'] = True
            finally: done.set()
        def invoke():
            try:
                task = webview.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.dumps(params))
                task.ContinueWith(Action[Task[String]](callback))
            except Exception:
                result['error'] = True
                done.set()
        webview.BeginInvoke(Action(invoke))
        if not done.wait(timeout) or result.get('error'):
            raise DeliveryError('平台浏览器操作超时，请核对草稿窗口。')
        return result.get('value', {})

    def login(self, progress):
        self._ensure_window()
        if self._call('state')['editing']:
            raise DeliveryError('小红书编辑窗口已有内容，请先暂存并关闭窗口，再执行新任务。')
        self._navigate(HOME)
        self.account = None
        progress('请在纸飞机弹出的小红书窗口登录；登录成功后会自动继续。')
        account = self._wait(lambda: self._call('identity'), '未完成小红书登录，请登录后重新执行。', seconds=300)
        self.account = account
        return account

    def inspect_save_state(self):
        """Read only bounded UI metadata; editor content is never returned."""
        raw = self._call('save-state')
        if not isinstance(raw, dict):
            raise DeliveryError('小红书保存状态结构未能核实。')
        state = {'path': raw.get('path') if raw.get('path') in ('/new/home', '/publish/publish', '/login') else 'other'}
        for key in ('ready', 'editing', 'images_ready', 'drawer', 'uploader_ready', 'home_ready'):
            state[key] = raw.get(key) is True
        for key in ('editor_total', 'editor_visible', 'title_visible', 'image_count', 'dialog_count'):
            value = raw.get(key)
            state[key] = value if type(value) is int and 0 <= value <= 10000 else 0
        labels = {'暂存离开', '保存草稿', '保存', '取消', '确定', '继续编辑', '退出', '返回', '发布', '确认发布'}
        state['buttons'] = [{'label': b['label'], 'visible': b.get('visible') is True, 'disabled': b.get('disabled') is True}
                            for b in raw.get('buttons', [])[:30] if isinstance(b, dict) and b.get('label') in labels]
        state['save_hosts'] = [{k: host.get(k) is True for k in ('visible', 'save_enabled', 'save_disabled', 'save_disabled_known', 'draft_label', 'handler')}
                               for host in raw.get('save_hosts', [])[:10] if isinstance(host, dict)]
        state['signals'] = [x for x in raw.get('signals', [])[:10] if x in ('saved', 'save_failed', 'image_uploading', 'retry_later')]
        self.diagnostics['save_state'] = state
        return state

    def diagnose(self):
        # This entry point must not create a window, navigate, click or save.
        if self.window is None or self.closed.is_set():
            return {'available': False}
        return {'available': True, 'save_state': self.inspect_save_state()}

    def _wait_saved_page(self):
        samples = 0
        def observe():
            nonlocal samples
            samples += 1
            state = self.inspect_save_state()
            self.diagnostics['save_state_samples'] = samples
            # A hidden stale editor is harmless only after a known landing UI
            # appears. An empty document or success toast alone is not proof.
            return (state['ready'] and not state['editing'] and state['dialog_count'] == 0
                    and (state['uploader_ready'] or state['home_ready'] or state['drawer']))
        self._wait(observe, '未确认草稿保存结果，请核对小红书草稿箱。')

    def verify_identity(self):
        """Read the existing page for <=10s, without navigation or login actions.

        Return an account when rendered, None for a confirmed login surface, or
        raise while the page remains unverified. Only confirmed sign-out clears
        the remembered account; an SPA loading race must not do that.
        """
        self._ensure_window()
        samples, errors, login_samples = 0, 0, 0
        last = {}
        message = '小红书账号信息尚未加载完成，请稍后重新核验；当前登录状态尚未确认。'

        def observe():
            nonlocal samples, errors, login_samples, last
            samples += 1
            try:
                state = self._call('identity-state')
                if not isinstance(state, dict):
                    raise DeliveryError('账号页面结构未能核实。')
            except DeliveryError:
                errors += 1
                login_samples = 0
                last = {}
                self.diagnostics['identity'] = {'samples': samples, 'read_errors': errors, 'status': 'unverified'}
                return None
            last = state
            account = state.get('account')
            valid = (isinstance(account, dict) and isinstance(account.get('id'), str)
                     and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', account['id'])
                     and isinstance(account.get('name'), str))
            login_samples = login_samples + 1 if state.get('ready') and state.get('loginPage') and not state.get('loading') else 0
            self.diagnostics['identity'] = {
                'samples': samples, 'read_errors': errors, 'ready': bool(state.get('ready')),
                'login_page': bool(state.get('loginPage')), 'loading': bool(state.get('loading')),
                'account_found': bool(valid), 'status': 'verified' if valid else 'unverified',
                'path': state.get('path') if state.get('path') in ('/new/home', '/login', '/login/', '/publish/publish') else 'other',
            }
            return account if valid else None

        try:
            account = self._wait(observe, message, seconds=10)
        except DeliveryError as exc:
            if str(exc) == message and login_samples >= 2 and last.get('loginPage'):
                self.diagnostics['identity']['status'] = 'signed-out'
                self.account = None
                return None
            raise
        self.account = account
        return account

    def _draft_box(self, *, phase='open', before=None, title=None):
        if not self._call('state')['drawer']:
            self._wait(lambda: self._call('state')['upload'], '小红书上传页面尚未加载。')
            self._call('box')
            self._wait(lambda: self._call('state')['drawer'], '草稿箱打开失败。')
        self._call('image-tab')
        self._wait(lambda: self._call('state')['imageTab'], '图文草稿列表尚未加载。')
        return self._read_draft_list(phase=phase, before=before, title=title)

    def _read_draft_list(self, *, phase, before=None, title=None):
        """Observe the open drawer for at most 10s; never invoke a save here.

        A baseline can be empty only when the page explicitly renders an empty
        state without a visible loading signal. Silence or a stable [] is not
        proof of an empty draft box. The post-save pass requires
        a unique new ID, then the caller must reopen it and verify all content.
        """
        previous = None
        samples = 0
        loading_seen = False
        self.diagnostics['stage'] = phase

        def observe():
            nonlocal previous, samples, loading_seen
            state = self._call('draft-state')
            if not isinstance(state, dict) or not isinstance(state.get('drafts'), list):
                raise DeliveryError('小红书草稿列表结构已变化，已停止自动操作。')
            drafts = state['drafts']
            if any(not isinstance(d, dict) or not isinstance(d.get('title'), str) for d in drafts):
                raise DeliveryError('小红书草稿列表结构已变化，已停止自动操作。')
            ids = [d.get('id') for d in drafts]
            valid_ids = [value for value in ids if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value)]
            missing = len(ids) - len(valid_ids)
            duplicate = len(valid_ids) - len(set(valid_ids))
            loading = bool(state.get('loading'))
            loading_seen = loading_seen or loading
            tab_count = state.get('tabCount')
            count_matches = tab_count is None or type(tab_count) is int and tab_count == len(drafts)
            ready = (bool(state.get('drawer')) and bool(state.get('imageTab')) and not loading
                     and count_matches and (bool(drafts) or bool(state.get('empty'))))
            matches = [d['title'] == title for d in drafts] if title is not None else []
            new_ids = [value for value in valid_ids if before is not None and value not in before]
            found = [d for d in drafts if d.get('id') in new_ids]
            samples += 1
            # Only identifiers, counts and booleans leave this method. Neither
            # other draft titles nor editor text/credentials enter diagnostics.
            self.diagnostics[phase] = {
                'origin': 'https://creator.xiaohongshu.com',
                'path': state.get('path') if state.get('path') in ('/publish/publish', '/new/home') else 'other',
                'samples': samples, 'drawer': bool(state.get('drawer')),
                'image_tab': bool(state.get('imageTab')), 'loading': loading,
                'loading_seen': loading_seen, 'empty': bool(state.get('empty')),
                'ready': ready, 'row_count': state.get('rowCount') if isinstance(state.get('rowCount'), int) else len(drafts),
                'image_count': len(drafts), 'tab_count': tab_count if type(tab_count) is int else None,
                'count_matches': count_matches, 'missing_id_count': missing,
                'duplicate_id_count': duplicate, 'ids': valid_ids,
                'title_matches': matches, 'new_ids': new_ids, 'candidate_count': len(found),
            }
            if not loading and (missing or duplicate or state.get('untypedCount')):
                raise DeliveryError('小红书草稿列表缺少唯一标识或结构已变化，已停止自动操作，请检查草稿箱。')
            if not ready:
                previous = None
                return None
            if before is not None and len(found) > 1:
                raise DeliveryError('未能唯一匹配刚保存的草稿，请检查草稿箱。')
            if before is not None and not found:
                previous = None
                return None
            # Two matching observations guard against a list replacement at the
            # end of rendering; explicit rows/empty-state evidence is still required.
            current = (tuple(ids), bool(state.get('empty')))
            if current != previous:
                previous = current
                return None
            return {'drafts': drafts}

        message = ('未能唯一匹配刚保存的草稿，请检查草稿箱。' if before is not None
                   else '未确认图文草稿列表加载完成，请检查草稿箱后重试。')
        return self._wait(observe, message, seconds=10)['drafts']

    def open_draft(self, receipt, progress):
        self._ensure_window()
        if self.current_draft and self.current_draft == receipt.get('draft_id') and self._call('state')['editing']:
            return receipt['account']
        account = self.login(progress)
        if receipt.get('account') and account['id'] != receipt['account']['id']:
            raise DeliveryError('当前小红书账号与这份草稿不一致，请切回原账号。')
        self._navigate(EDITOR)
        self._draft_box()
        if receipt.get('draft_id'):
            self._call('edit-draft', receipt['draft_id'])
            self._wait(lambda: self._call('state')['editing'], '草稿没有打开，请在窗口中核对。')
            self.current_draft = receipt['draft_id']
        return account

    def save_draft(self, note, paths, expected_account, progress):
        touched = False
        self.diagnostics = {'stage': 'starting', 'save_attempted': False, 'readback_verified': False}
        try:
            account = self.login(progress)
            if expected_account and account['id'] != expected_account:
                raise DeliveryError('小红书账号已变化，请切回创建任务时的账号再发送。')
            self.diagnostics['account_id'] = account['id']
            self._navigate(EDITOR)
            drafts = self._draft_box(phase='before', title=note['title'])
            if len(drafts) >= 99:
                raise DeliveryError('小红书图文草稿箱接近上限，请先整理，避免平台自动删除旧草稿。')
            if any(d['title'] == note['title'] for d in drafts):
                raise DeliveryError('小红书已有同名草稿，请先核对，纸飞机不会新建重复草稿。', uncertain=True)
            before = {d['id'] for d in drafts}
            self._call('close-box')
            progress('正在上传小红书图集，随后自动填写标题和文案。')
            root = self._cdp('DOM.getDocument', {})['root']['nodeId']
            node = self._cdp('DOM.querySelector', {'nodeId': root, 'selector': 'input[type="file"]'})['nodeId']
            if not node: raise DeliveryError('找不到小红书图片上传入口。')
            # From here the platform may create a draft even without an explicit save.
            touched = True
            self.diagnostics['stage'] = 'upload'
            self._cdp('DOM.setFileInputFiles', {'nodeId': node, 'files': paths})
            self._wait(lambda: (s := self._call('state'))['editing'] and s['images'] == len(paths) and s['imagesReady'],
                       '图片尚未全部上传完成，请核对小红书窗口。', seconds=180)
            # Browser input events update the platform's controlled title state.
            # Assigning input.value only changed its display in the live editor.
            self._call('focus-title')
            self._cdp('Input.insertText', {'text': note['title']})
            self._call('focus-caption')
            self._cdp('Input.insertText', {'text': note['caption']})
            def matches():
                s = self._call('state')
                evidence = {'title_match': s['title'] == note['title'],
                            'title_length': len(s.get('title') or ''), 'expected_title_length': len(note['title']),
                            'caption_match': s['caption'] == note['caption'].strip(),
                            'image_count': s['images'], 'expected_image_count': len(paths),
                            'images_ready': bool(s['imagesReady'])}
                self.diagnostics['content_check'] = evidence
                return evidence['title_match'] and evidence['caption_match'] and s['images'] == len(paths) and evidence['images_ready']
            self._wait_editor_settled(matches)
            progress('正在保存草稿并重新打开核对。')
            self.diagnostics['stage'] = 'save'
            self.diagnostics['save_control_before'] = self.inspect_save_state()
            self.diagnostics['save_attempted'] = True
            save_result = self._call('save')
            if isinstance(save_result, dict) and save_result.get('method') in ('visible-button', 'visible-component'):
                self.diagnostics['save_method'] = save_result['method']
            self._wait_saved_page()
            # Save may return to home rather than to the uploader.
            self._navigate(EDITOR)
            after = self._draft_box(phase='after', before=before, title=note['title'])
            # Drawer labels can be truncated or decorated; only the one newly
            # observed ID is opened, and full editor content is still verified.
            found = [d for d in after if d['id'] not in before]
            if len(found) != 1:
                raise DeliveryError('未能唯一匹配刚保存的草稿，请检查草稿箱。')
            draft_id = found[0]['id']
            self.diagnostics['matched_draft_id'] = draft_id
            self.diagnostics['stage'] = 'readback'
            self._call('edit-draft', draft_id)
            self._wait(matches, '草稿重新打开后的内容不一致，请检查后再发表。')
            self.current_draft = draft_id
            self.diagnostics.update(stage='verified', readback_verified=True, result='verified')
            self.window.set_title('纸飞机 · 小红书草稿已保存，请检查并确认发布')
            return {'draft_id': draft_id, 'account': account, 'verified': True}
        except Exception as exc:
            message = str(exc) if isinstance(exc, DeliveryError) else '小红书操作异常，请核对草稿窗口。'
            uncertain = touched or isinstance(exc, DeliveryError) and exc.uncertain
            self.diagnostics['result'] = 'uncertain' if uncertain else 'failed'
            raise DeliveryError(message, uncertain=uncertain) from None
