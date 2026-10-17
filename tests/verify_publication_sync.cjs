const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHmac}=require('node:crypto');
const script=fs.readFileSync('web/platform_transport.js','utf8');
function run(platform, fixtures, seen=[]){
  return vm.runInNewContext('('+script+')',{location:{origin:platform==='csdn'?'https://editor.csdn.net':'https://juejin.cn'},crypto:webcrypto,URL,TextEncoder,Uint8Array,AbortSignal,btoa,fetch:async(url,options)=>{
    seen.push([url,options]); const fixture=fixtures.find(([path])=>new URL(url).pathname===path);
    if(!fixture)throw Error('Unexpected endpoint '+url);
    const data=typeof fixture[1]==='function'?fixture[1](options,url):fixture[1];
    return {ok:true,json:async()=>data};
  }});
}
function juejin(audit=2, change=()=>{}){
  const draft={id:'123',article_id:'456',user_id:'owner'},article={article_id:'456',article_info:{article_id:'456',draft_id:'123',user_id:'owner',audit_status:audit},author_user_info:{user_id:'owner'}};
  change(draft,article);
  return run('juejin',[
    ['/content_api/v1/article_draft/detail',o=>{assert.equal(JSON.parse(o.body).draft_id,'123');return {err_no:0,data:{article_draft:draft}};}],
    ['/content_api/v1/article/detail',o=>{const data=JSON.parse(o.body);assert.equal(data.article_id,'456');assert.equal(data.forbid_count,true);return {err_no:0,data:article};}]
  ]);
}
const arg={draft_id:'123',account_id:'owner'};
(async()=>{
  let result=await juejin()('juejin','publication',arg);
  assert.equal(result.data.state,'published');assert.equal(result.data.url,'https://juejin.cn/post/456');
  for(const status of [0,1,3])assert.equal((await juejin(status)('juejin','publication',arg)).data.state,'reviewing');
  for(const status of [-1,4,undefined,'2'])assert.equal((await juejin(status,(_,a)=>a.article_info.audit_status=status)('juejin','publication',arg)).data.state,'unknown');
  assert.equal((await juejin(2,d=>d.article_id='0')('juejin','publication',arg)).data.state,'draft');
  for(const change of [d=>d.id='other',(d,a)=>a.article_info.draft_id='777',(d,a)=>a.article_info.article_id='777',(d,a)=>a.article_id='777',(d,a)=>a.author_user_info.user_id='different',d=>d.user_id='different',d=>delete d.user_id])
    assert.equal((await juejin(2,change)('juejin','publication',arg)).ok,false);
  assert.equal((await run('juejin',[['/content_api/v1/article_draft/detail',{err_no:1}]])('juejin','publication',arg)).ok,false);
  function csdn(change=()=>{}){
    const base={name:'owner',blog_url:'https://blog.csdn.net/owner'},article={article_id:123,status:1,read_type:'public'},listing={list:[{articleId:123,status:'1'}],total:1};
    change(base,article,listing);
    return run('csdn',[
      ['/blog-console-api/v3/editor/getBaseInfo',{code:200,data:base}],
      ['/blog-console-api/v3/editor/getArticle',o=>{
        const h=o.headers,sign='GET\n*/*\n\n\n\nx-ca-key:203803574\nx-ca-nonce:'+h['x-ca-nonce']+'\n/blog-console-api/v3/editor/getArticle?id=123&model_type';
        assert.equal(h['x-ca-signature'],createHmac('sha256','9znpamsyl2c7cdrr9sas0le9vbc3r6ba').update(sign).digest('base64'));
        return {code:200,data:article};}],
      ['/blog/phoenix/console/v1/article/list',(o,url)=>{
        const expected=article.status===2?'draft':[7,16,6,9,99].includes(article.status)?'audit':article.status===1?'all_v2':'all_v3';
        assert.equal(new URL(url).searchParams.get('status'),expected);
        return {code:200,data:listing};}]
    ]);
  }
  result=await csdn()('csdn','publication',arg);
  assert.equal(result.data.state,'published');assert.equal(result.data.url,'https://blog.csdn.net/owner/article/details/123');
  for(const status of [7,16])assert.equal((await csdn((b,a,l)=>{a.status=status;l.list[0].status=String(status)})('csdn','publication',arg)).data.state,'reviewing');
  assert.equal((await csdn((b,a,l)=>{a.status=2;l.list[0].status='2'})('csdn','publication',arg)).data.state,'draft');
  for(const change of [(b,a)=>a.read_type='private',(b,a)=>delete a.read_type,(b,a)=>a.status=6,(b,a)=>a.status=64,(b,a)=>a.read_need_vip=true,b=>b.is_muser=true,(b,a,l)=>l.list=[],(b,a,l)=>l.list[0].status='16'])
    assert.equal((await csdn(change)('csdn','publication',arg)).data.state,'unknown');
  for(const change of [b=>b.name='other',(b,a)=>a.article_id=999,b=>b.blog_url='https://evil.example/owner',b=>b.blog_url='https://user:password@blog.csdn.net/owner'])
    assert.equal((await csdn(change)('csdn','publication',arg)).ok,false);
  const app=fs.readFileSync('web/app.js','utf8');
  const source=app.slice(app.indexOf('function platformDraftJobs('),app.indexOf('function jobRow('));
  const state={jobs:[{id:'job',post_id:'post',channel_id:'juejin',channel_kind:'bridge',status:'draft',url:'https://juejin.cn/editor/drafts/123',publication_sync:{supported:true,state:'reviewing',message:'审核中 <unsafe>',checked_at:'2026-10-07'}}]};
  const ui=vm.runInNewContext(source+';({platformDraftMenu,platformDraftLinks})',{state,esc:s=>String(s).replaceAll('<','&lt;'),safeLink:s=>s,fmt:s=>s});
  const post={id:'post',target_channel:'juejin'};
  assert.ok(ui.platformDraftMenu(post).includes('立即同步发表状态'));
  assert.ok(ui.platformDraftMenu(post).includes('手动确认已发布（备用）'));
  assert.ok(ui.platformDraftLinks(post).includes('&lt;unsafe>'));
  state.jobs[0].status='published';state.jobs[0].public_url='https://juejin.cn/post/456';
  assert.ok(ui.platformDraftLinks(post).includes('查看已发表文章'));
  assert.ok(!ui.platformDraftMenu(post).includes('立即同步发表状态'));
  console.log('Publication readback: exact IDs, account binding, audit states, failure protection and UI fallback passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
