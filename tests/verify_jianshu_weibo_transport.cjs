'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
let passed=0;
function response(data) {return {ok:true,status:200,json:async()=>data,text:async()=>String(data)};}
function load(platform,fetch,origin) {
  return vm.runInNewContext('('+fs.readFileSync('web/'+platform+'_transport.js','utf8')+')',{
    location:{origin:origin || (platform==='jianshu'?'https://www.jianshu.com':'https://card.weibo.com')},
    document:{querySelector:()=>({content:'csrf-fixture'})},fetch,crypto:webcrypto,
    AbortSignal,Uint8Array,URL,URLSearchParams,Blob,FormData,atob,btoa});
}
async function test(name,run) {await run();passed++;console.log('PASS '+name);}
const article={account_id:'42',title:'图文核验',markdown:'文章 **正文**',content:'<p>文章 <strong>正文</strong></p>',summary:'摘要'};
function jianshu(options={}) {
  const calls=[],state={body:null,identities:0};
  const fetch=async(url,o={})=>{
    calls.push({url,...o});
    if(url==='/author/current_user' || url==='/settings/basic.json') {
      state.identities++;
      const uid=options.switched && state.identities>=options.switched?'99':'42';
      if(url==='/author/current_user')return options.noIdentity?{ok:false,status:401}:response({id:uid,nickname:'简书测试',avatar:options.defaultAvatar?'https://cdn2.jianshu.io/assets/default_avatar.png':''});
      return response({data:{id:uid,nickname:'简书测试',avatar:''}});
    }
    if(url==='/author/notebooks')return response(options.emptyBooks?[]:[{id:7}]);
    if(url==='/author/notes') {
      assert.equal(o.method,'POST');assert.equal(o.headers['X-CSRF-Token'],'csrf-fixture');
      assert.deepEqual(JSON.parse(o.body),{notebook_id:'7',title:article.title,at_bottom:false});
      if(options.createTimeout)throw Error('timeout');
      return response({id:81});
    }
    if(url.startsWith('/author/notebooks/7/notes?')) {
      if(options.listFail)throw Error('offline');
      return response([{id:81,note_type:options.noteType??2,autosave_control:4,shared:options.shared??false,
        schedule_publish_at:options.scheduled?1791500000:null,title:options.badTitle?'wrong':article.title}]);
    }
    if(url==='/author/notes/81') {
      assert.equal(o.method,'PUT');state.body=JSON.parse(o.body);
      assert.equal(state.body.id,'81');assert.equal(state.body.autosave_control,5);
      assert.equal(state.body.title,article.title);
      if(options.saveFail)throw Error('timeout');
      return response({content_updated_at:'2026-10-08'});
    }
    if(url==='/author/notes/81/content') {
      assert.equal(o.cache,'no-store');
      if(options.readFail)throw Error('offline');
      return response({content:options.badContent?'changed':state.body.content});
    }
    if(url.startsWith('/upload_images/token.json?'))return response({token:'temporary-token',key:'upload_images/42-fixture.png'});
    if(url==='https://upload.qiniup.com') {
      assert.equal(o.method,'POST');assert.equal(o.credentials,'omit');
      assert.equal(o.body.get('token'),'temporary-token');assert.equal(o.body.get('x:protocol'),'https');
      assert.equal(await o.body.get('file').text(),'fixture');
      return response({url:options.badImage?'https://evil.invalid/fixture.png':'https://upload-images.jianshu.io/upload_images/42-fixture.png'});
    }
    throw Error('Unexpected request '+url);
  };
  return {run:load('jianshu',fetch),calls,state};
}
function weibo(options={}) {
  const calls=[],state={body:null,identities:0};
  const fetch=async(url,o={})=>{
    calls.push({url,...o});
    if(url==='/article/v5/editor') {
      state.identities++;
      const uid=options.switched && state.identities>=options.switched?'99':'42';
      const config=JSON.stringify({uid,nick:"测试 O'Brien"}).replaceAll('\\','\\\\').replaceAll("'","\\'");
      return response(options.noIdentity?'<html>login</html>':"<script>config: JSON.parse('"+config+"')</script>");
    }
    const u=new URL(url,'https://card.weibo.com');
    if(u.pathname.startsWith('/article/v5/aj/editor/')) {
      assert.equal(u.searchParams.get('uid'),'42');
      assert.equal(u.searchParams.get('_rid').length,43);
      if(u.pathname.endsWith('/draft/create')) {
        assert.equal(o.method,'POST');assert.equal(o.body.toString(),'');
        if(options.createTimeout)throw Error('timeout');
        return response({code:100000,data:{id:'81'}});
      }
      if(u.pathname.endsWith('/draft/save')) {
        assert.equal(o.method,'POST');assert.equal(u.searchParams.get('id'),'81');
        assert.equal(o.headers['SN-REQID'],u.searchParams.get('_rid'));
        state.body=Object.fromEntries(o.body);
        assert.equal(state.body.status,'0');assert.equal(state.body.save,'1');
        assert.equal(state.body.publish_at,'');assert.equal(state.body.follow_to_read,'0');assert.equal(state.body.isreward,'0');
        if(options.saveFail)throw Error('timeout');
        return response({code:100000,data:{}});
      }
      if(u.pathname.endsWith('/draft/load')) {
        assert.equal(o.method,undefined);assert.equal(u.searchParams.get('id'),'81');assert.equal(o.cache,'no-store');
        if(options.readFail)throw Error('timeout');
        return response({code:100000,data:{...state.body,uid:options.badUid?'99':'42',id:options.badId?'82':'81',
          content:options.badContent?'changed':state.body.content,status:options.public?'1':'0'}});
      }
      if(u.pathname.endsWith('/plugins/uploadpic')) {
        assert.equal(o.body.get('pid'),'fixtureImage123');assert.equal(o.body.get('uid'),'42');
        if(options.attachFail)return response({code:500002});
        return response({code:100000,data:{}});
      }
    }
    if(u.hostname==='picupload.weibo.com') {
      assert.equal(o.method,'POST');assert.equal(await o.body.text(),'fixture');
      return response({code:'A00006',data:{pics:{pic_1:{pid:'fixtureImage123',large:{url:options.badImage?'https://evil.invalid/img.jpg':'https://wx3.sinaimg.cn/large/fixtureImage123.jpg'}}}}});
    }
    throw Error('Unexpected request '+url);
  };
  return {run:load('weibo',fetch),calls,state};
}
function writes(calls) {return calls.filter(c=>['POST','PUT'].includes(c.method));}
function privateOnly(calls) {assert.ok(calls.every(c=>!/(?:publicize|privatize|draft\/publish|statuses\/update)/.test(c.url)));}
(async()=>{
  await test('Jianshu verifies current identity',async()=>assert.equal((await jianshu().run('jianshu','identity')).data.id,'42'));
  await test('Jianshu canonical user identity accepts default avatar without parsing an ID from it',async()=>{
    const t=jianshu({defaultAvatar:true}),r=await t.run('jianshu','identity');
    assert.equal(r.data.id,'42');assert.equal(r.diagnostic.source,'/author/current_user');assert.equal(t.calls.length,1);
  });
  await test('Jianshu identity failure diagnostics expose only field names and fixed endpoint status',async()=>{
    const run=load('jianshu',async url=>url==='/author/current_user'?response({nickname:'private-name',avatar:'private-avatar'}):response({data:{nickname:'private-name',avatar:'https://cdn2.jianshu.io/default.png'}}));
    const r=await run('jianshu','identity');assert.equal(r.ok,false);assert.equal(r.diagnostic.status,200);
    assert.equal(r.diagnostic.source,'/settings/basic.json');assert.ok(!JSON.stringify(r).includes('private-name'));assert.ok(!JSON.stringify(r).includes('private-avatar'));
  });
  await test('Jianshu unauthorized canonical identity stays signed out',async()=>{
    const t=jianshu({noIdentity:true}),r=await t.run('jianshu','identity');assert.equal(r.ok,true);assert.equal(r.data,null);assert.equal(r.diagnostic.status,401);assert.equal(t.calls.length,1);
  });
  await test('Jianshu preserves Markdown format and reads private draft',async()=>{
    const t=jianshu(),r=await t.run('jianshu','save',article);assert.equal(r.ok,true);assert.equal(r.data.incomplete,undefined);
    assert.equal(r.data.url,'https://www.jianshu.com/writer#/notebooks/7/notes/81');assert.equal(t.state.body.content,article.markdown);
    assert.equal(writes(t.calls).length,2);privateOnly(t.calls);
  });
  await test('Jianshu honors rich-text format without changing account settings',async()=>{
    const t=jianshu({noteType:1}),r=await t.run('jianshu','save',article);assert.equal(r.data.incomplete,undefined);
    assert.equal(t.state.body.content,article.content);assert.equal(writes(t.calls).length,2);
  });
  await test('Jianshu includes separate cover in the private draft',async()=>{
    const t=jianshu(),cover='https://upload-images.jianshu.io/upload_images/42-fixture.png';
    assert.equal((await t.run('jianshu','save',{...article,cover})).data.incomplete,undefined);
    assert.equal(t.state.body.content,'![]('+cover+')\n\n'+article.markdown);
  });
  for(const [name,options] of Object.entries({readFail:{readFail:true},badContent:{badContent:true},badTitle:{badTitle:true},saveFail:{saveFail:true},listFail:{listFail:true},publicDraft:{shared:true},scheduledDraft:{scheduled:true},unknownFormat:{noteType:7},accountSwitched:{switched:3}})) {
    await test('Jianshu retains draft receipt on '+name,async()=>{
      const t=jianshu(options),r=await t.run('jianshu','save',article);assert.equal(r.data.id,'81');assert.equal(r.data.incomplete,true);
      assert.equal(t.calls.filter(c=>c.url==='/author/notes').length,1);privateOnly(t.calls);
    });
  }
  await test('Jianshu unknown creation outcome is not retried',async()=>{
    const t=jianshu({createTimeout:true});assert.equal((await t.run('jianshu','save',article)).ok,false);assert.equal(writes(t.calls).length,1);
  });
  await test('Jianshu changed account and missing notebook block before creation',async()=>{
    for(const [t,arg] of [[jianshu(),{...article,account_id:'9'}],[jianshu({emptyBooks:true}),article]]) {
      assert.equal((await t.run('jianshu','save',arg)).ok,false);assert.equal(writes(t.calls).length,0);
    }
  });
  await test('Jianshu image upload uses temporary grant and no Qiniu cookies',async()=>{
    const t=jianshu();assert.equal((await t.run('jianshu','upload',{base64:btoa('fixture'),extension:'png',mime:'image/png'})).data,'https://upload-images.jianshu.io/upload_images/42-fixture.png');privateOnly(t.calls);
  });
  await test('Jianshu rejects invalid image destinations',async()=>assert.equal((await jianshu({badImage:true}).run('jianshu','upload',{base64:btoa('fixture'),extension:'png',mime:'image/png'})).ok,false));
  await test('Weibo parses quoted nickname without executing script',async()=>{
    const r=await weibo().run('weibo','identity');assert.equal(r.data.id,'42');assert.equal(r.data.name,"测试 O'Brien");
  });
  await test('Weibo saves and fresh-reads a private headline article',async()=>{
    const t=weibo(),r=await t.run('weibo','save',article);assert.equal(r.ok,true);assert.equal(r.data.incomplete,undefined);
    assert.equal(r.data.url,'https://card.weibo.com/article/v5/editor#/draft/81');assert.equal(writes(t.calls).length,2);privateOnly(t.calls);
  });
  await test('Weibo sets image pid while preserving content and cover',async()=>{
    const t=weibo(),cover='https://wx3.sinaimg.cn/large/fixtureImage123.jpg';
    const r=await t.run('weibo','save',{...article,cover,content:article.content+'<p><img src="'+cover+'"></p>'});
    assert.equal(r.data.incomplete,undefined);assert.ok(t.state.body.content.includes('data-pid="fixtureImage123"'));assert.equal(t.state.body.cover,cover);
  });
  for(const [name,options] of Object.entries({readFail:{readFail:true},badContent:{badContent:true},badId:{badId:true},badUid:{badUid:true},saveFail:{saveFail:true},publicResult:{public:true},accountSwitched:{switched:3}})) {
    await test('Weibo retains exact receipt on '+name,async()=>{
      const t=weibo(options),r=await t.run('weibo','save',article);assert.equal(r.data.id,'81');assert.equal(r.data.incomplete,true);
      assert.equal(t.calls.filter(c=>c.url.includes('/draft/create?')).length,1);privateOnly(t.calls);
    });
  }
  await test('Weibo unknown creation outcome is not retried',async()=>{
    const t=weibo({createTimeout:true});assert.equal((await t.run('weibo','save',article)).ok,false);assert.equal(writes(t.calls).length,1);
  });
  await test('Weibo image upload registers its verified picture id',async()=>{
    const t=weibo(),r=await t.run('weibo','upload',{base64:btoa('fixture'),extension:'png',mime:'image/png'});
    assert.equal(r.data,'https://wx3.sinaimg.cn/large/fixtureImage123.jpg');assert.equal(writes(t.calls).length,2);privateOnly(t.calls);
  });
  await test('Weibo rejected image or attachment never returns success',async()=>{
    for(const options of [{badImage:true},{attachFail:true}])assert.equal((await weibo(options).run('weibo','upload',{base64:btoa('fixture'),extension:'png',mime:'image/png'})).ok,false);
  });
  await test('Weibo rejects images without platform upload receipt before creation',async()=>{
    const t=weibo();assert.equal((await t.run('weibo','save',{...article,content:'<img src="https://external.invalid/image.jpg">'})).ok,false);assert.equal(writes(t.calls).length,0);
  });
  await test('Both platforms reject local images, account mismatch and foreign origin',async()=>{
    for(const platform of ['jianshu','weibo']) {
      const factory=platform==='jianshu'?jianshu:weibo;
      for(const arg of [{...article,account_id:'9'},{...article,markdown:'/media/x.png',content:'<img src="/media/x.png">'}]) {
        const t=factory();assert.equal((await t.run(platform,'save',arg)).ok,false);assert.equal(writes(t.calls).length,0);
      }
      const run=load(platform,()=>{throw Error('must not request');},'https://evil.invalid');assert.equal((await run(platform,'save',article)).ok,false);
    }
  });
  await test('Weibo picture window uploads only to its exact same origin with browser credentials',async()=>{
    const calls=[];
    const run=load('weibo',async(url,o)=>{
      calls.push({url,...o});assert.equal(new URL(url).origin,'https://picupload.weibo.com');
      assert.equal(new URL(url).pathname,'/interface/pic_upload.php');assert.equal(o.credentials,'include');
      assert.equal(o.redirect,'error');assert.equal(o.method,'POST');assert.equal(o.headers,undefined);
      assert.equal(o.body.type,'image/png');assert.equal(await o.body.text(),'fixture');
      return response({code:'A00006',data:{pics:{pic_1:{pid:'fixtureImage123',large:{url:'https://wx3.sinaimg.cn/large/fixtureImage123.jpg'}}}},extra:'not forwarded'});
    },'https://picupload.weibo.com');
    const r=await run('weibo','upload_binary',{account_id:'42',base64:btoa('fixture'),extension:'png',mime:'text/html',url:'https://evil.invalid/'});
    assert.equal(r.ok,true);assert.equal(r.data.data.pics.pic_1.pid,'fixtureImage123');assert.equal(r.data.extra,undefined);assert.equal(calls.length,1);
  });
  await test('Weibo binary action cannot run in editor, login or unrelated origin',async()=>{
    for(const origin of ['https://card.weibo.com','https://weibo.com','https://picupload.weibo.com.evil.invalid']) {
      let called=false;const run=load('weibo',async()=>{called=true;},origin);
      assert.equal((await run('weibo','upload_binary',{account_id:'42',base64:btoa('fixture'),extension:'png'})).ok,false);assert.equal(called,false);
    }
  });
  await test('Weibo picture window cannot create drafts or query account',async()=>{
    for(const action of ['identity','save','upload','upload_receipt']) {
      let called=false;const run=load('weibo',async()=>{called=true;},'https://picupload.weibo.com');
      assert.equal((await run('weibo',action,article)).ok,false);assert.equal(called,false);
    }
  });
  await test('Weibo binary upload rejects oversized and empty images without network',async()=>{
    for(const base64 of ['',Buffer.alloc(5_000_001).toString('base64')]) {
      let called=false;const run=load('weibo',async()=>{called=true;},'https://picupload.weibo.com');
      assert.equal((await run('weibo','upload_binary',{account_id:'42',base64,extension:'png'})).ok,false);assert.equal(called,false);
    }
  });
  await test('Weibo cross-window receipt registers once under the same verified account',async()=>{
    const t=weibo(),r=await t.run('weibo','upload_receipt',{account_id:'42',result:{code:'A00006',data:{pics:{pic_1:{pid:'fixtureImage123'}}}}});
    assert.equal(r.data,'https://wx3.sinaimg.cn/large/fixtureImage123.jpg');assert.equal(writes(t.calls).length,1);privateOnly(t.calls);
  });
  await test('Weibo account change prevents cross-window receipt attachment',async()=>{
    const t=weibo({switched:2}),r=await t.run('weibo','upload_receipt',{account_id:'42',result:{code:'A00006',data:{pics:{pic_1:{pid:'fixtureImage123'}}}}});
    assert.equal(r.ok,false);assert.equal(writes(t.calls).length,0);
  });
  await test('Both uploads reject a changed account without returning a usable image',async()=>{
    for(const [platform,factory] of [['jianshu',jianshu],['weibo',weibo]]) {
      const t=factory({switched:2});assert.equal((await t.run(platform,'upload',{base64:btoa('fixture'),extension:'png',mime:'image/png'})).ok,false);
    }
  });
  console.log(passed+' Jianshu/Weibo isolated transport checks passed. No real account or draft was touched.');
})().catch(error=>{console.error(error);process.exitCode=1;});
