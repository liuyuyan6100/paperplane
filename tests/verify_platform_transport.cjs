const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto,createHmac,createHash} = require('node:crypto');
const script=fs.readFileSync('web/platform_transport.js','utf8');
function transport(platform,handle) {
  return vm.runInNewContext('('+script+')',{location:{origin:platform==='csdn'?'https://editor.csdn.net':'https://juejin.cn'},
    crypto:webcrypto,TextEncoder,Uint8Array,URL,Blob,FormData,AbortSignal,atob,btoa,fetch:handle});
}
function response(data,headers={}) {return {ok:true,headers:new Headers(headers),json:async()=>data};}
function signatureCheck(url,o) {
  const h=o.headers,date=h['x-amz-date'],day=date.slice(0,8),scope=day+'/cn-north-1/imagex/aws4_request';
  const u=new URL(url),query=[...u.searchParams].sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
  const digest=s=>createHash('sha256').update(s).digest('hex');
  const canonical=[o.method,'/',query,`x-amz-date:${date}\nx-amz-security-token:session\n`,'x-amz-date;x-amz-security-token',digest('')].join('\n');
  let key='AWS4secret';for(const part of [day,'cn-north-1','imagex','aws4_request'])key=createHmac('sha256',key).update(part).digest();
  const sig=createHmac('sha256',key).update('AWS4-HMAC-SHA256\n'+date+'\n'+scope+'\n'+digest(canonical)).digest('hex');
  assert.ok(h.Authorization.endsWith('Signature='+sig));
}
(async()=>{
  const appSource=fs.readFileSync('web/app.js','utf8');
  const panelSource=appSource.slice(appSource.indexOf('function publishingBridgePanel('),appSource.indexOf('async function handlePublishingBridgeAction('));
  const panel=vm.runInNewContext(panelSource+';publishingBridgePanel',{deployment:'local',esc:s=>String(s).replaceAll('<','&lt;')});
  const html=panel({desktop:true,accounts:{csdn:{name:'<Test>'},juejin:null},busy:false});
  assert.equal((html.match(/data-action="publishing-open"/g)||[]).length,3);
  assert.ok(!html.includes('chromewebstore'));
  assert.ok(html.includes('&lt;Test>'));
  let calls=[];
  let run=transport('csdn',async(url,o)=>{
    calls.push([url,o]);
    if(url.endsWith('getBaseInfo'))return response({code:200,data:{name:'user',nickname:'User'}});
    const body=JSON.parse(o.body);assert.equal(body.status,2);assert.equal(body.pubStatus,'draft');assert.deepEqual(body.cover_images,['https://img-blog.csdnimg.cn/a.png']);
    return response({code:200,data:{id:123}});
  });
  assert.equal((await run('csdn','identity')).data.id,'user');
  const article={title:'Title',markdown:'Body',content:'<p>Body</p>',cover:'https://img-blog.csdnimg.cn/a.png'};
  assert.equal((await run('csdn','save',article)).data.id,'123');
  const request=calls[1][1],h=request.headers;
  const sign='POST\n*/*\n\napplication/json\n\nx-ca-key:203803574\nx-ca-nonce:'+h['x-ca-nonce']+'\n/blog-console-api/v3/mdeditor/saveArticle';
  assert.equal(h['x-ca-signature'],createHmac('sha256','9znpamsyl2c7cdrr9sas0le9vbc3r6ba').update(sign).digest('base64'));
  run=transport('juejin',async(url,o)=>{
    if(url.includes('gen_token'))return response({data:{token:{AccessKeyId:'access',SecretAccessKey:'secret',SessionToken:'session'}}});
    if(url.includes('ApplyImageUpload')){signatureCheck(url,o);return response({Result:{UploadAddress:{StoreInfos:[{StoreUri:'test/path',Auth:'upload'}],UploadHosts:['example.volces.com'],SessionKey:'session/key'}}});}
    if(url.startsWith('https://example.volces.com')){assert.equal(o.headers['Content-CRC32'],'cbf43926');return response({});}
    if(url.includes('CommitImageUpload')){signatureCheck(url,o);return response({Result:{Results:[{Uri:'test/path',UriStatus:2000}]}});}
    if(url.includes('get_img_url'))return response({data:{main_url:'https://p3.byteimg.com/test.png'}});
    if(url.endsWith('/sys/token'))return response(null,{'x-ware-csrf-token':'0,token,1,success'});
    if(url.endsWith('/article_draft/create')){const body=JSON.parse(o.body);assert.equal(body.cover_image,article.cover);assert.equal(o.headers['x-secsdk-csrf-token'],'token');return response({data:{id:'456'}});}
    throw Error('unexpected endpoint');
  });
  assert.equal((await run('juejin','upload',{base64:btoa('123456789'),extension:'png',mime:'image/png'})).data,'https://p3.byteimg.com/test.png');
  assert.equal((await run('juejin','save',article)).data.url,'https://juejin.cn/editor/drafts/456');
  assert.equal((await run('csdn','save',article)).ok,false,'wrong origin blocked');
  run=transport('csdn',async()=>response({code:200,data:{host:'https://evil.example',customParam:{}}}));
  assert.equal((await run('csdn','upload',{base64:btoa('fixture'),extension:'png',mime:'image/png'})).ok,false,'untrusted upload host blocked');
  console.log('Platform protocol checks passed: signing, uploads, CRC, draft-only, CSRF, origin and destination validation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
