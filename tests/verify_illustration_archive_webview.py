"""Isolated real-DOM checks for illustration archive UI; no Studio or real data."""
import json
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / 'work/test-results/illustration-archive'
OUT.mkdir(parents=True, exist_ok=True)

FIXTURE = r"""
window.$=s=>document.querySelector(s);
window.esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
window.heading=(a,b,c='')=>'<header><h1>'+esc(a)+'</h1><p>'+esc(b)+'</p>'+c+'</header>';
window.empty=(a,b)=>'<h2>'+esc(a)+'</h2><p>'+esc(b)+'</p>';
window.memorySnapshotHtml=()=>'';
window.view='illustrations';window.calls=[];window.toasts=[];window.modalCalls=0;
window.toast=s=>toasts.push(s);window.modal=()=>{modalCalls++;};window.refresh=async()=>{};
window.closeModal=()=>{};window.dirty=false;
const id='a'.repeat(32),postId='b'.repeat(32),busyId='c'.repeat(32),candidateId='d'.repeat(32);
const page={title:'Synthetic picture',text:'Image text',prompt:'Test prompt',slot:'cover',aspect:'landscape'};
window.fixture={id,post_id:postId,status:'review',version:3,source_version:7,post_version:8,archived_at:'',error:'',
 context:{post:{title:'Synthetic official article'},profile:null,references:[]},pages:[page],
 candidates:[{id:candidateId,page_index:0,status:'review',context:{page},image_url:'/media/fixture.png',error:'',plan_changed:false}],
 adopted:[{candidate_id:candidateId,page_index:0,image_url:'/media/fixture.png',slot:'cover'}]};
window.state={posts:[{id:postId,title:'Synthetic official article',body:'Preserve original body',cover:'/media/fixture.png',version:8,stage:'ready',archived:false}],channels:[]};
window.originalArticle=JSON.stringify(state.posts);window.originalHistory=JSON.stringify([fixture.pages,fixture.candidates,fixture.adopted]);
window.rejectArchive=false;window.holdArchive=false;window.releaseArchive=null;
window.api=async(path,values)=>{
 calls.push({path,values:values===undefined?null:structuredClone(values)});
 if(path==='/illustrations/settings')return {use_shared:true};
 if(path==='/illustrations'||path==='/illustrations?archived=1'){
  const archived=path.includes('?');
  return {plans:[fixture,{id:busyId,post_id:postId,title:'Busy fixture',status:'running',version:1,archived_at:''}]
    .filter(p=>!!p.archived_at===archived).map(p=>({...structuredClone(p),title:p.id===id?'Synthetic official article':p.title}))};
 }
 if(path==='/illustrations/'+id)return structuredClone(fixture);
 if(path==='/illustrations/'+id+'/archive'){
  if(rejectArchive)throw Error('409 fixture conflict');
  if(holdArchive)await new Promise(resolve=>releaseArchive=resolve);
  if(values.version!==fixture.version)throw Error('409 version conflict');
  fixture.archived_at=values.archived?'2026-10-09T00:00:00Z':'';
  return structuredClone(fixture);
 }
 throw Error('Unexpected fixture API '+path);
};
"""

CHECKS = r"""
(async()=>{
 const checks=[];
 const check=(value,name)=>{if(!value)throw Error(name);checks.push(name);};
 const writes=()=>calls.filter(c=>c.values!==null);
 const action=name=>document.querySelector('[data-action="'+name+'"]');
 const rejects=async(fn,pattern,name)=>{let error;try{await fn();}catch(e){error=e;}check(error&&pattern.test(error.message),name);};
 location.hash='#illustrations';await drawIllustrations();
 check(calls.some(c=>c.path==='/illustrations'),'list defaults to unarchived API');
 check(action('illustration-list-active').getAttribute('aria-pressed')==='true','active filter indicated');
 const busy=document.querySelector('[data-action="illustration-archive"][data-id="'+busyId+'"]');
 check(busy.disabled,'running plan archive disabled');
 const archive=document.querySelector('[data-action="illustration-archive"][data-id="'+id+'"]');
 check(archive.dataset.version==='3'&&archive.dataset.archived==='true','archive uses exact content version');
 sessionStorage.setItem('illustration:'+id,JSON.stringify({version:3,pages:[{...page,title:'Unsaved local text'}]}));
 await rejects(()=>handleIllustrationAction(archive),/保存/,'list archive preserves pending local draft');
 check(writes().length===0&&sessionStorage.getItem('illustration:'+id).includes('Unsaved local text'),'no write or draft deletion when blocked');
 sessionStorage.removeItem('illustration:'+id);
 await handleIllustrationAction(archive);
 check(writes().length===1&&writes()[0].path.endsWith('/archive')&&writes()[0].values.archived===true,'one archive request without confirmation');
 check(!document.querySelector('[data-id="'+id+'"]'),'archived plan removed from active list');
 check(modalCalls===0,'reversible archive has no confirmation dialog');
 await handleIllustrationAction(action('illustration-list-archived'));
 check(calls.at(-1).path==='/illustrations?archived=1','archive filter requests only archived plans');
 check(action('illustration-archive').dataset.archived==='false','archive list offers restore');
 location.hash='#illustrations/'+id;await drawIllustrations();
 check(document.querySelectorAll('[data-illustration-field]').length===5&&[...document.querySelectorAll('[data-illustration-field]')].every(n=>n.disabled),'archived plan fields are read-only');
 check(!action('illustration-save')&&!action('illustration-improve')&&!action('illustration-generate')&&!action('illustration-adopt')&&!action('illustration-revise'),'archived plan has no editing or candidate mutation controls');
 check(document.querySelector('#illustration-candidates img').getAttribute('src')==='/media/fixture.png'&&$('#illustration-candidates').textContent.includes('当前已采用'),'archived history and original image remain visible');
 for(const name of ['save','improve','add-page','remove-page','generate','adopt','confirm-adopt','revise','confirm-revise'])
  await rejects(()=>handleIllustrationAction({dataset:{action:'illustration-'+name}}),/归档/,'archived action blocked: '+name);
 check(writes().length===1,'archived action guards send no mutations');
 await handleIllustrationAction(action('illustration-archive'));
 check(!fixture.archived_at&&action('illustration-save')&&action('illustration-adopt'),'restore re-enables normal editing and adoption');
 check([...document.querySelectorAll('[data-illustration-field]')].every(n=>!n.disabled),'restored fields enabled');
 $('#illustration-title-0').value='Current unsaved title';illustrationDraft();
 const before=writes().length;
 await rejects(()=>handleIllustrationAction(action('illustration-archive')),/保存/,'dirty detail cannot be archived');
 check(writes().length===before&&$('#illustration-title-0').value==='Current unsaved title'&&sessionStorage.getItem('illustration:'+id).includes('Current unsaved title'),'dirty text and recovery copy preserved');
 illustrationDirty=false;sessionStorage.removeItem('illustration:'+id);await drawIllustrations();
 fixture.candidates[0].status='running';await drawIllustrations();
 check(action('illustration-archive').disabled,'running candidate blocks detail archive');
 await rejects(()=>handleIllustrationAction(action('illustration-archive')),/生成中/,'candidate generation guard refuses API write');
 check(writes().length===before,'running candidate archive sends no request');
 fixture.candidates[0].status='review';await new Promise(resolve=>setTimeout(resolve,2200));
 check(!action('illustration-archive').disabled,'completed candidate polling enables archive without reopening');
 rejectArchive=true;
 await rejects(()=>handleIllustrationAction(action('illustration-archive')),/409/,'server archive conflict shown without retry');
 check(!illustrationArchiving&&!$('#illustration-title-0').disabled&&action('illustration-save'),'archive rejection restores existing controls');
 check(writes().length===before+1,'archive rejection never retries');
 rejectArchive=false;holdArchive=true;
 const pending=handleIllustrationAction(action('illustration-archive'));
 check(illustrationArchiving&&$('#illustration-title-0').disabled&&action('illustration-save').disabled,'in-flight archive prevents typing from being discarded');
 await rejects(()=>handleIllustrationAction(action('illustration-archive')),/正在保存/,'duplicate in-flight archive blocked');
 releaseArchive();await pending;holdArchive=false;
 check(fixture.archived_at&&writes().length===before+2,'one archive submission after in-flight guard');
 sessionStorage.setItem('illustration:'+id,JSON.stringify({version:3,pages:[{...page,title:'Preserved old editor text'}]}));
 await drawIllustrations();
 check(!illustrationDirty&&$('#illustration-title-0').value===page.title&&sessionStorage.getItem('illustration:'+id).includes('Preserved old editor text'),'archived read-only view does not revive or delete saved local editing');
 await handleIllustrationAction(action('illustration-archive'));
 check(illustrationDirty&&$('#illustration-title-0').value==='Preserved old editor text','restoring recovers local unsaved editing');
 check(JSON.stringify(state.posts)===originalArticle&&JSON.stringify([fixture.pages,fixture.candidates,fixture.adopted])===originalHistory,'article images and all candidate/adoption history unchanged');
 check(fixture.status==='review'&&fixture.version===3,'archive and restore preserve status and content version');
 check(writes().every(c=>c.path==='/illustrations/'+id+'/archive'),'UI writes only the fixed archive endpoint');
 return {ok:true,checks};
})().then(result=>window.archiveResult=result).catch(error=>window.archiveResult={ok:false,error:error.stack});true
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'<!doctype html><meta charset="utf-8"><main id="main"></main>')

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory(dir=OUT, ignore_cleanup_errors=True) as temp:
    window = webview.create_window('Isolated illustration archive verification', f'http://127.0.0.1:{server.server_port}', hidden=True)

    def verify():
        import time
        result = {'ok': False}
        try:
            assert window.events.loaded.wait(30)
            window.evaluate_js(FIXTURE+'\n'+(ROOT/'web/illustrations.js').read_text(encoding='utf-8')+'\n'+CHECKS)
            end = time.monotonic()+30
            while time.monotonic()<end:
                value = window.evaluate_js('window.archiveResult || null')
                if value:
                    result = value
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('Isolated UI checks did not complete')
        except Exception:
            result['error'] = traceback.format_exc()
        finally:
            (OUT/'webview-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False))
            window.destroy()

    webview.start(verify, gui='edgechromium', private_mode=False, storage_path=str(Path(temp)/'browser'))
server.shutdown()
server.server_close()
if not json.loads((OUT/'webview-result.json').read_text(encoding='utf-8'))['ok']:
    raise SystemExit(1)
