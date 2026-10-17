const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const origins={toutiao:'https://mp.toutiao.com','bilibili-article':'https://member.bilibili.com'};
function harness(platform,scenario={}) {
  const filename=platform==='toutiao'?'toutiao_transport.js':'bilibili_transport.js';
  const title='平台草稿回读验收',content='<h2>第一节</h2><p>完整正文与图片</p>';
  const account={id:'12345',name:'作者'},id='7694567890123456789';
  const isToutiao=platform==='toutiao',draftId=isToutiao?id:'4321';
  const remote=isToutiao?'https://p3.toutiaoimg.com/pgc-image/photo':'https://i0.hdslb.com/bfs/article/photo.png';
  const article={title,content,account_id:account.id,summary:'测试摘要',tags:['测试']};
  const calls=[];let identityCalls=0;
  const respond=data=>({ok:true,status:200,json:async()=>data,text:async()=>{
    const json=JSON.stringify(data);
    return scenario.numericId?json.replaceAll('"'+draftId+'"',draftId):json;
  }});
  const state={body:null};
  const ctx={location:{origin:origins[platform],pathname:'/profile_v4/graphic/publish'},
    URL,URLSearchParams,Blob,FormData,Uint8Array,atob,btoa,AbortSignal,
    document:{cookie:'bili_jct='+'a'.repeat(32)},
    fetch:async(url,options={})=>{
      calls.push({url,options});
      assert.equal(options.credentials,'include');assert.equal(options.redirect,'error');
      if(url.includes('get_media_info')||url.includes('/web-interface/nav')) {
        identityCalls++;
        if(scenario.identityFailure)return respond(isToutiao?{code:100004}:{code:-101});
        const mid=scenario.switchAccount && identityCalls>1?'55555':account.id;
        return respond(isToutiao?{code:0,data:{user:{id:mid,screen_name:account.name}}}:{code:0,data:{isLogin:true,mid,uname:account.name}});
      }
      if(url.includes('upload_picture')||url.includes('/article/upcover')) {
        assert.ok(options.body instanceof FormData);
        assert.ok(options.body.get(isToutiao?'upfile':'binary') instanceof Blob);
        if(!isToutiao)assert.equal(options.body.get('csrf'),'a'.repeat(32));
        const urlValue=scenario.badImage?'https://evil.example/photo.png':scenario.missingImage?undefined:remote;
        return respond(isToutiao?{state:'SUCCESS',url:urlValue,origin_web_uri:'pgc-image/photo',width:1200,height:800}:{code:0,data:{url:urlValue,size:100}});
      }
      if(url.includes('/article/publish?')||url.includes('/draft/addupdate')) {
        state.body=new URLSearchParams(options.body);
        assert.equal(options.method,'POST');
        assert.equal(state.body.get('save'),'0');
        assert.equal(state.body.get('title'),title);assert.equal(state.body.get('content'),content);
        if(isToutiao){assert.equal(state.body.get('pgc_id'),'');assert.equal(state.body.get('timer_status'),'0');assert.equal(state.body.get('article_ad_type'),'2');}
        else{assert.equal(state.body.has('pgc_id'),false);assert.equal(state.body.has('aid'),false);assert.equal(state.body.get('csrf'),'a'.repeat(32));assert.equal(state.body.get('banner_url'),article.cover||'');assert.equal(state.body.get('image_urls'),article.cover||'');assert.equal(state.body.get('origin_image_urls'),article.cover||'');assert.equal(state.body.get('tid'),article.cover?'4':'5');}
        if(scenario.timeout)throw Error('timed out after submission');
        if(scenario.writeRejection)return respond({code:7050});
        if(scenario.noId)return respond({code:0,data:{}});
        return respond({code:0,data:isToutiao?{pgcId:draftId}:{aid:draftId}});
      }
      if(url.includes('/article/edit?')||url.includes('/draft/view?')) {
        assert.equal(options.method,undefined,'read-back is a GET');
        assert.ok(url.includes(draftId));
        if(scenario.readError)throw Error('read unavailable');
        const readTitle=scenario.mismatch?'another draft':title;
        const readContent=scenario.missingContent?undefined:scenario.truncated||scenario.rootSource?'<p>missing the full article</p>':scenario.jsonContent?JSON.stringify({ops:[{insert:'hidden fixture content'}]}):content;
        const readId=scenario.missingReadId?undefined:scenario.wrongReadId?'999':draftId;
        return respond({code:0,data:isToutiao?{...(scenario.notDraft?{is_draft:false}:{}),...(scenario.rootSource?{content:content}:scenario.wrongRootSource?{content:'wrong source'}:{}),articlePgc:{pgc_id:readId,title:readTitle,content:readContent,
          pgc_feed_covers:scenario.missingCover?[]:JSON.parse(state.body.get('pgc_feed_covers'))}}:
          {id:readId,title:readTitle,content:readContent,type:scenario.jsonContent?4:0,state:0,template_id:4,banner_url:scenario.missingCover?'':article.cover||'',csrf_token:'do-not-return-secret',session:{cookie:'do-not-return-cookie'}}});
      }
      throw Error('Unexpected endpoint '+url);
    }};
  const run=vm.runInNewContext('('+fs.readFileSync('web/'+filename,'utf8')+')',ctx);
  return {run:(action,arg=article)=>run(platform,action,arg),article,calls,state,remote,draftId,ctx};
}
(async()=>{
  let count=0;
  for(const platform of ['toutiao']) {
    const image={extension:'png',mime:'image/png',base64:btoa('png-fixture')};
    let h=harness(platform),r=await h.run('identity');assert.equal(r.data.id,'12345');count++;
    h=harness(platform,{identityFailure:true});assert.equal((await h.run('identity')).data,null);count++;
    h=harness(platform);assert.equal((await h.run('upload',image)).data,h.remote);count++;
    for(const scenario of [{badImage:true},{missingImage:true}]){h=harness(platform,scenario);assert.equal((await h.run('upload',image)).ok,false);count++;}
    h=harness(platform);h.ctx.location.origin='https://evil.example';assert.equal((await h.run('save')).ok,false);assert.equal(h.calls.length,0);count++;
    h=harness(platform);h.article.content+=' /media/local.png';assert.equal((await h.run('save')).ok,false);assert.equal(h.calls.length,0);count++;
    h=harness(platform);h.article.account_id='999';assert.equal((await h.run('save')).ok,false);assert.ok(!h.state.body);count++;
    h=harness(platform);r=await h.run('save');assert.equal(r.ok,true);assert.equal(r.data.id,h.draftId);assert.equal(r.data.incomplete,undefined);count++;
    h=harness(platform);h.article.cover=(await h.run('upload',image)).data;r=await h.run('save');assert.equal(r.data.incomplete,undefined);assert.ok(h.state.body.get(platform==='toutiao'?'pgc_feed_covers':'banner_url').includes(h.remote));count++;
    for(const scenario of [{readError:true},{mismatch:true},{missingContent:true},{truncated:true},{missingReadId:true},{wrongReadId:true},{switchAccount:true}]) {
      h=harness(platform,scenario);r=await h.run('save');assert.equal(r.ok,true);assert.equal(r.data.id,h.draftId);assert.equal(r.data.incomplete,true);assert.ok(r.data.url.includes(h.draftId));assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1,'no second write after uncertain read');count++;
    }
    h=harness(platform,{missingCover:true});h.article.cover=(await h.run('upload',image)).data;r=await h.run('save');assert.equal(r.data.incomplete,true);count++;
    for(const scenario of [{timeout:true},{noId:true},{writeRejection:true}]) {h=harness(platform,scenario);r=await h.run('save');assert.equal(r.ok,false);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1,'never retry an uncertain or rejected write');count++;}
    // Recheck an existing exact ID without creating, updating or uploading anything.
    for(const scenario of [{},{readError:true},{mismatch:true},{missingContent:true},{truncated:true},{missingReadId:true},{wrongReadId:true},{switchAccount:true}]) {
      h=harness(platform,scenario);
      h.article.draft_id=h.draftId;
      h.state.body=new URLSearchParams({pgc_feed_covers:'[]'});
      r=await h.run('draft-read');
      assert.equal(r.ok,true);assert.equal(r.data.id,h.draftId);
      assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0,'read-only recovery never writes');
      if(Object.keys(scenario).length){
        assert.equal(r.data.incomplete,true);assert.ok(r.data.reason,'failure carries a specific reason');
        const diagnostic=JSON.stringify(r.data.observed);
        assert.ok(!diagnostic.includes(h.article.content));assert.ok(!diagnostic.includes(h.article.title));
        assert.ok(!diagnostic.includes('do-not-return'));assert.ok(!diagnostic.includes('csrf_token'));assert.ok(!diagnostic.includes('session'));
        if(!scenario.readError)assert.ok(r.data.observed.structure.length);
      } else assert.equal(r.data.incomplete,undefined);
      count++;
    }
    h=harness(platform);h.article.draft_id='bad-id';r=await h.run('draft-read');assert.equal(r.ok,false);assert.equal(h.calls.length,0);count++;
    h=harness(platform);h.article.draft_id=h.draftId;h.article.account_id='999';r=await h.run('draft-read');assert.equal(r.ok,false);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);count++;
    h=harness(platform);r=await h.run('publication');assert.equal(r.ok,false);assert.equal(h.calls.length,0,'unimplemented publication never guesses');count++;
  }
  for(const scenario of [{numericId:true},{rootSource:true},{numericId:true,rootSource:true}]) {
    const h=harness('toutiao',scenario);h.article.draft_id=h.draftId;h.state.body=new URLSearchParams({pgc_feed_covers:'[]'});
    const r=await h.run('draft-read');assert.equal(r.ok,true);assert.equal(r.data.incomplete,undefined);assert.equal(r.data.id,h.draftId);
    assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);count++;
  }
  {
    const h=harness('toutiao',{numericId:true});const r=await h.run('save');assert.equal(r.ok,true);assert.equal(r.data.id,h.draftId);assert.equal(r.data.incomplete,undefined);count++;
  }
  {
    const h=harness('toutiao',{wrongRootSource:true});h.article.draft_id=h.draftId;h.state.body=new URLSearchParams({pgc_feed_covers:'[]'});
    const r=await h.run('draft-read');assert.equal(r.data.incomplete,true);assert.equal(r.data.observed.content_field,'root.content');
    assert.ok(r.data.observed.content_candidates.article);assert.ok(r.data.observed.cover_structure);count++;
  }
  {
    const h=harness('toutiao',{notDraft:true});h.article.draft_id=h.draftId;h.state.body=new URLSearchParams({pgc_feed_covers:'[]'});
    const r=await h.run('draft-read');assert.equal(r.data.incomplete,true);assert.equal(r.data.observed.is_draft,false);assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);count++;
  }
  // Native cover evidence remains independent of body verification, and only
  // schema/key comparisons may be returned for signed image URLs.
  for(const cover of [
    {url:'https://p3.toutiaoimg.com/pgc-image/photo?format=private-transform-value'},
    {origin_uri:'pgc-image/photo',url:'https://evil.example/pgc-image/photo'},
    {origin_uri:'pgc-image/other',url:'https://p3.toutiaoimg.com/pgc-image/other~thumb'}
  ]) {
    const h=harness('toutiao');h.article.draft_id=h.draftId;h.article.cover=h.remote;
    h.state.body=new URLSearchParams({pgc_feed_covers:JSON.stringify([cover])});
    const r=await h.run('draft-read');assert.equal(r.data.incomplete,true);
    assert.equal(r.data.observed.cover_matches,false);
    assert.ok(r.data.observed.cover_candidates[0]);
    const diagnostic=JSON.stringify(r.data.observed.cover_candidates);
    assert.ok(!diagnostic.includes('private-transform-value'));
    assert.ok(!diagnostic.includes('https://'));
    if(cover.url.includes('?format='))assert.equal(r.data.observed.cover_candidates[0].url.actual_path.key,'/pgc-image/photo');
    if(cover.url.includes('?format='))assert.deepEqual(Array.from(r.data.observed.cover_candidates[0].url.differing_query_keys),['format']);
    assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);count++;
  }
  const bili=fs.readFileSync('web/bilibili_transport.js','utf8');
  new vm.Script('('+bili+')');
  assert.ok(bili.includes('/x/dynamic/feed/article/draft/view?article_id='));
  assert.ok(bili.includes('/x/dynamic/feed/article/draft/add?csrf='));
  assert.ok(!bili.includes('/draft/addupdate'));
  assert.ok(!bili.includes('/feed/create/opus'));count++;
  const t=harness('toutiao');t.ctx.location.pathname='/profile_v4/';assert.equal((await t.run('save')).ok,false);assert.equal(t.calls.length,0);count++;
  console.log(count+' checks passed: Toutiao non-DOM contracts and Bili native endpoint contract. Full Bili DOM/native protocol coverage is in tools/verify_toutiao_bilibili_webview.py.');
})().catch(e=>{console.error(e);process.exitCode=1;});
