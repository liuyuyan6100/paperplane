"""Run Jianshu/Weibo transports in isolated WebView2; no account or remote writes."""
import json
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import webview
from desktop_xhs import DesktopXhsBrowser

OUT = ROOT.parent / 'work/test-results/jianshu-weibo'
OUT.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"code":"fixture"}')

    def log_message(self, *args):
        pass


FIXTURE = r"""
window.mode='ok';window.writes=0;window.saved=null;window.actualOrigin='';
window.article={account_id:'42',title:'WebView2 fixture',markdown:'Body',content:'<p>Body</p>',cover:''};
window.fetch=async(url,options={})=>{
  const reply=x=>({ok:true,status:200,json:async()=>x,text:async()=>String(x)});
  if(options.method && options.method!=='GET')writes++;
  if(url==='/author/current_user')return reply({id:'42',nickname:'Fixture',avatar:'https://cdn2.jianshu.io/default.png'});
  if(url==='/settings/basic.json')return reply({data:{id:'42',nickname:'Fixture'}});
  if(url==='/author/notebooks')return reply([{id:7}]);
  if(url==='/author/notes')return reply({id:81});
  if(url.startsWith('/author/notebooks/7/notes?'))return reply([{id:81,title:article.title,note_type:2,autosave_control:4,shared:mode==='public'}]);
  if(url==='/author/notes/81'){saved=JSON.parse(options.body);return reply({});}
  if(url==='/author/notes/81/content')return reply({content:mode==='changed'?'other':saved.content});
  if(url.startsWith('/upload_images/token.json'))return reply({token:'fixture',key:'upload_images/fixture.png'});
  if(url==='https://upload.qiniup.com'){
    if(options.credentials!=='omit' || !(options.body instanceof FormData) || options.body.get('file').size!==3)throw Error('multipart mismatch');
    return reply({url:'https://upload-images.jianshu.io/upload_images/fixture.png'});
  }
  if(url==='/article/v5/editor')return reply("config: JSON.parse('{\"uid\":\"42\",\"nick\":\"Fixture\"}')");
  if(url.includes('/draft/create?'))return reply({code:100000,data:{id:81}});
  if(url.includes('/draft/save?')){saved=Object.fromEntries(options.body);if(saved.status!=='0' || saved.publish_at!=='')throw Error('not private');return reply({code:100000,data:{}});}
  if(url.includes('/draft/load?'))return reply({code:100000,data:{...saved,id:81,status:mode==='public'?'1':'0',content:mode==='changed'?'other':saved.content}});
  if(url.startsWith('https://picupload.weibo.com/interface/pic_upload.php?')){
    if(options.credentials!=='include' || options.redirect!=='error' || !(options.body instanceof Blob) || options.body.size!==3 || options.headers)throw Error('binary boundary mismatch');
    return reply({code:'A00006',data:{pics:{pic_1:{pid:'fixtureImage123'}}}});
  }
  if(url.includes('/plugins/uploadpic?'))return reply({code:100000,data:{}});
  throw Error('unexpected endpoint '+url);
};true
"""

with tempfile.TemporaryDirectory(dir=OUT, ignore_cleanup_errors=True) as tmp:
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = webview.create_window('简书微博隔离验证', f'http://127.0.0.1:{server.server_port}/json', hidden=True)
    browser = DesktopXhsBrowser()
    browser.window = window
    browser._check = lambda: None
    result = {'ok': False, 'checks': []}

    def evaluate(expression):
        reply = browser._cdp('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
        assert not reply.get('exceptionDetails'), reply
        return reply.get('result', {}).get('value')

    def verify():
        try:
            assert window.events.loaded.wait(30)
            window.evaluate_js(FIXTURE)
            for platform, origin in [('jianshu', 'https://www.jianshu.com'), ('weibo', 'https://card.weibo.com')]:
                script = (ROOT / 'web' / (platform + '_transport.js')).read_text(encoding='utf-8-sig')
                script = script.replace('location.origin', 'window.actualOrigin')
                window.evaluate_js('window.actualOrigin=' + json.dumps(origin) + ';window.transport=(' + script + ');true')

                def call(action, argument='article'):
                    return evaluate('transport(' + json.dumps(platform) + ',' + json.dumps(action) + ',' + argument + ')')

                assert call('identity')['data']['id'] == '42'
                window.evaluate_js("mode='ok';writes=0")
                assert call('save')['data'].get('incomplete') is not True
                assert window.evaluate_js('writes') == 2
                for mode in ('changed', 'public'):
                    window.evaluate_js('mode=' + json.dumps(mode))
                    assert call('save')['data']['incomplete'] is True
                window.evaluate_js("mode='ok'")
                assert call('upload', "{base64:'YWJj',extension:'png',mime:'image/png'}")['ok'] is True
                result['checks'].extend([platform + '-identity', platform + '-private-draft', platform + '-uncertain-receipt', platform + '-image'])
                if platform == 'weibo':
                    window.evaluate_js("actualOrigin='https://picupload.weibo.com';writes=0")
                    receipt = call('upload_binary', "{account_id:'42',base64:'YWJj',extension:'png'}")
                    assert receipt['data']['data']['pics']['pic_1']['pid'] == 'fixtureImage123'
                    assert call('save')['ok'] is False
                    window.evaluate_js("actualOrigin='https://card.weibo.com'")
                    assert call('upload_binary', "{account_id:'42',base64:'YWJj',extension:'png'}")['ok'] is False
                    assert window.evaluate_js('writes') == 1
                    result['checks'].extend(['json-document-execution', 'same-origin-binary', 'origin-guards'])
            result['ok'] = True
        except Exception:
            result['error'] = traceback.format_exc()
        finally:
            (OUT / 'webview-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False))
            window.destroy()

    webview.start(verify, gui='edgechromium', private_mode=False, storage_path=str(Path(tmp) / 'browser'))
    server.shutdown()
    server.server_close()
if not result['ok']:
    raise SystemExit(1)
