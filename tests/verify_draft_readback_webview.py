"""Isolated real-DOM protocol verification. No accounts or remote network are used."""
import json
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webview

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_xhs import DesktopXhsBrowser
OUT = ROOT.parent / 'work/test-results/draft-readback'
OUT.mkdir(parents=True, exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'<title>Isolated draft receipt verification</title>')
    def log_message(self, *args):
        pass

FIXTURE = r"""
window.scenario='ok';window.writes=0;window.saved=null;window.calls=[];
window.article={account_id:'456',draft_id:'123',title:'Fixture title',markdown:'Line one\n\n![image](https://static.oschina.net/image.png)',cover:''};
window.pageState=(draft=false)=>({global:{sessionInfo:{login:true,key:'prefix~fixture'},sessionUser:{user:{id:'456',name:'Fixture'}}},
  ...(draft?{editor:{detail:{draft:{id:scenario==='wrong-id'?'999':'123',title:article.title,text:scenario==='bad-body'?'truncated':article.markdown,
    object_id:0,type_str:'article',cover:article.cover}}}}:{})});
window.stateHtml=state=>'<script id="__NEXT_DATA__" type="application/json">'+JSON.stringify({props:{pageProps:{initialState:state}}})+'</script>';
window.fetch=async(url,options={})=>{
  calls.push([url,options.method||'GET']);
  if(options.method && options.method!=='GET')writes++;
  const reply=data=>({ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data)});
  if(platform==='oschina'){
    const osc=value=>reply({code:200,result:value});
    if(url.endsWith('/user/myDetails'))return osc({userId:scenario==='wrong-account'?999:456,userVo:{name:'Fixture'}});
    if(url.endsWith('/save_draft')){saved=JSON.parse(options.body);return osc({id:123});}
    if(url.endsWith('/edit/detail?id=123')){
      if(scenario==='read-error')throw Error('offline');
      return osc({draft:scenario==='wrong-id'?999:123,space:456,title:article.title,
        content:scenario==='bad-body'?'truncated':article.markdown,contentType:'1'});
    }
  }
  if(platform==='segmentfault'){
    if(url==='/user/settings')return {ok:true,url:'https://segmentfault.com/user/settings',text:async()=>stateHtml(pageState())};
    if(url==='/gateway/draft'){
      if(options.headers.token!=='fixture' || options.headers.authorization!=='Bearer fixture')throw Error('incorrect auth');
      saved=JSON.parse(options.body);return reply({id:'123'});
    }
    if(url==='/write?draftId=123'){
      if(scenario==='read-error')throw Error('offline');
      const state=pageState(true);if(scenario==='wrong-account')state.global.sessionUser.user.id='999';
      return {ok:true,url:'https://segmentfault.com/write?draftId=123',text:async()=>stateHtml(state)};
    }
  }
  if(platform==='51cto'){
    if(url==='/blogger/publish')return {ok:true,url:'https://blog.51cto.com/blogger/publish',text:async()=>'<meta name="csrf-token" content="fixture"><input id="is_old" value="0"><li class="more user"><a href="/456"><img></a></li><script>var submitForm = '+JSON.stringify({is_old:0,did:'',blog_id:'',title:'',content:'',banner_type:0,img_urls:[],import_type:''})+';</script>'};
    if(url==='/blogger/draft'){saved=Object.fromEntries(new URLSearchParams(options.body));return reply({status:'1',data:{did:'123'}});}
    if(url==='/blogger/draft/123'){
      if(scenario==='read-error')throw Error('offline');
      const doc=document.implementation.createHTMLDocument('fixture');
      const title=doc.createElement('input');title.name='title';title.setAttribute('value',article.title);doc.body.append(title);
      const body=doc.createElement('textarea');body.name='content';body.textContent=scenario==='bad-body'?'truncated':article.markdown;doc.body.append(body);
      return {ok:true,url:'https://blog.51cto.com/blogger/draft/'+(scenario==='wrong-id'?'999':'123'),text:async()=>doc.documentElement.outerHTML};
    }
  }
  throw Error('unexpected request '+url);
};true
"""

with tempfile.TemporaryDirectory(dir=OUT, ignore_cleanup_errors=True) as tmp:
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = webview.create_window('草稿回读隔离测试', f'http://127.0.0.1:{server.server_port}', hidden=True)
    results = {'ok': False, 'checks': []}
    browser = DesktopXhsBrowser()
    browser.window = window
    browser._check = lambda: None
    def evaluate(expression):
        result = browser._cdp('Runtime.evaluate', {'expression':expression, 'awaitPromise':True, 'returnByValue':True})
        assert not result.get('exceptionDetails'), result
        return result.get('result', {}).get('value')
    def verify():
        try:
            assert window.events.loaded.wait(30)
            for platform, filename, origin in (
                ('oschina', 'oschina_transport.js', 'https://my.oschina.net'),
                ('segmentfault', 'segmentfault_transport.js', 'https://segmentfault.com'),
                ('51cto', 'cto51_transport.js', 'https://blog.51cto.com')):
                script=(ROOT/'web'/filename).read_text(encoding='utf-8').replace('location.origin',json.dumps(origin))
                window.evaluate_js('window.platform='+json.dumps(platform)+';true')
                window.evaluate_js(FIXTURE)
                window.evaluate_js('window.transport=('+script+');true')
                def call(action='draft-read', argument='article'):
                    return evaluate('window.transport(platform,'+json.dumps(action)+','+argument+')')
                reply=call()
                assert reply['ok'] and reply['data'].get('incomplete') is not True, (platform,reply)
                assert window.evaluate_js('writes')==0
                for scenario in ('bad-body','wrong-id','read-error'):
                    window.evaluate_js('scenario='+json.dumps(scenario))
                    reply=call()
                    assert reply['ok'] and reply['data']['incomplete'] is True, (platform,scenario,reply)
                    assert reply['data']['reason']
                assert window.evaluate_js('writes')==0
                window.evaluate_js("scenario='ok'")
                reply=call(argument="({...article,account_id:'999'})")
                assert reply['ok'] is False
                assert window.evaluate_js('writes')==0
                reply=call('save')
                assert reply['ok'] and reply['data'].get('incomplete') is not True, (platform,reply)
                assert window.evaluate_js('writes')==1
                results['checks'].append(platform+': read-only original receipt, account/body/id mismatch, read failure, draft-only save')
            script=(ROOT/'web/zhihu_transport.js').read_text(encoding='utf-8').replace('location.origin',json.dumps('https://zhuanlan.zhihu.com'))
            window.evaluate_js('window.transport=('+script+');window.imageLoaded=true;window.Image=class{set src(v){if(v){this.naturalWidth=imageLoaded?1:0;this.naturalHeight=imageLoaded?1:0;queueMicrotask(()=>this.onload());}}};true')
            assert evaluate("transport('zhihu','image-verify',{url:'https://pic4.zhimg.com/fixture'})")['data'] is True
            proof=evaluate("transport('zhihu','image-verify',{url:'https://pic4.zhimg.com/fixture.png',diagnostics:true})")['data']
            assert proof=={'ready':True,'reason':'loaded','width':1,'height':1},proof
            assert evaluate("transport('zhihu','image-verify',{url:'https://picx.zhimg.com/50/fixture_1440w.jpg?source=fixture',diagnostics:true})")['data']['ready'] is True
            window.evaluate_js('imageLoaded=false')
            assert evaluate("transport('zhihu','image-verify',{url:'https://pic4.zhimg.com/fixture'})")['data'] is False
            assert evaluate("transport('zhihu','image-verify',{url:'https://evil.example/fixture'})")['ok'] is False
            results['checks'].append('zhihu: exact CDN image decode and foreign-host rejection')
            results['ok']=True
        except Exception:
            results['error']=traceback.format_exc()
        finally:
            (OUT/'webview-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(results,ensure_ascii=False))
            window.destroy()
    webview.start(verify, gui='edgechromium', private_mode=False, storage_path=str(Path(tmp)/'browser'))
    server.shutdown()
    server.server_close()
if not results['ok']:
    raise SystemExit(1)
