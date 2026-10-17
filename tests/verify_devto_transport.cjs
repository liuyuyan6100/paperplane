const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/devto_transport.js','utf8');
const articleImage={account_id:'456',base64:'YWJj',extension:'png',mime:'image/png'};
function fixture(scenario='ok', url='https://dev-to-uploads.s3.amazonaws.com/uploads/articles/fixture.png') {
  let reads=0,writes=0;
  const requests=[];
  const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
  const call=vm.runInNewContext('('+source+')',{
    location:{origin:'https://dev.to'},URL,Uint8Array,Blob,FormData,AbortSignal,atob,
    fetch:async(path,options)=>{
      requests.push([path,options]);
      if(path==='/async_info/base_data'){
        reads++;
        assert.equal(options.method,undefined);
        assert.equal(options.credentials,'same-origin');
        assert.equal(options.cache,'no-store');
        assert.equal(options.redirect,'error');
        if(scenario==='network')throw Error('offline');
        if(scenario==='http-error')return response({},503);
        if(scenario==='malformed')return response({unrelated:true});
        if(scenario==='signed-out')return response({token:'fixture-csrf'});
        const user={id:scenario==='wrong-account'||scenario==='changed-after-upload'&&reads>1?999:456,name:'Fixture'};
        return response({token:'fixture-csrf',user:scenario==='object-user'?user:JSON.stringify(user)});
      }
      if(path==='/image_uploads'){
        writes++;
        assert.equal(options.method,'POST');
        assert.equal(options.credentials,'same-origin');
        assert.equal(options.redirect,'error');
        assert.equal(options.headers['X-CSRF-Token'],'fixture-csrf');
        assert.equal(options.headers['Content-Type'],undefined,'multipart boundary comes from browser');
        assert.equal(options.body.get('authenticity_token'),'fixture-csrf');
        assert.equal(options.body.get('image[]').size,3);
        assert.equal(options.body.get('image[]').type,'image/png');
        assert.equal(options.body.get('image[]').name,'image.png');
        if(scenario==='upload-timeout')throw Error('timeout');
        if(scenario==='rejected')return response({error:'rejected'},422);
        if(scenario==='extra-link')return response({links:[url,url]});
        return response({links:[url]});
      }
      throw Error('Unexpected request '+path);
    }
  });
  return {call,requests,reads:()=>reads,writes:()=>writes};
}
(async()=>{
  let checks=0;
  for(const scenario of ['ok','object-user']){
    const f=fixture(scenario),r=await f.call('devto','identity');
    assert.equal(r.ok,true);assert.equal(r.data.id,'456');assert.equal(r.data.name,'Fixture');
    assert.equal(Object.keys(r.data).length,2,'identity must not expose csrf or full user record');assert.equal(f.writes(),0);checks++;
  }
  const signedOut=await fixture('signed-out').call('devto','identity');assert.equal(signedOut.data,null);checks++;
  for(const scenario of ['network','http-error','malformed']){
    const result=await fixture(scenario).call('devto','identity');assert.equal(result.ok,false);checks++;
  }
  for(const url of [
    'https://dev-to-uploads.s3.amazonaws.com/uploads/articles/fixture.png',
    'https://dev.to/remoteimages/uploads/articles/fixture.png',
    'https://dev.to/uploads/articles/fixture.png',
    'https://thepracticaldev.s3.amazonaws.com/uploads/articles/fixture.png',
    'https://media.dev.to/fixture.png',
    'https://media2.dev.to/dynamic/image/fixture.png',
    'https://dev-to-uploads.s3.us-east-2.amazonaws.com/uploads/articles/fixture.png',
    'https://res.cloudinary.com/practicaldev/image/upload/fixture.png']){
    const f=fixture('ok',url),r=await f.call('devto','upload',articleImage);
    assert.equal(r.ok,true,JSON.stringify(r));assert.equal(r.data,url);assert.equal(f.writes(),1);assert.equal(f.reads(),2);checks++;
  }
  for(const url of [
    'http://dev-to-uploads.s3.amazonaws.com/uploads/articles/x.png',
    'https://dev-to-uploads.s3.amazonaws.com.evil.test/uploads/articles/x.png',
    'https://dev.to/settings',
    'https://dev.to:123/remoteimages/uploads/articles/x.png',
    'https://user:password@dev.to/remoteimages/uploads/articles/x.png',
    'https://res.cloudinary.com/unrelated/image/upload/x.png',
    'https://media2.dev.to.evil.test/x.png',
    'https://attacker.s3.us-east-2.amazonaws.com/uploads/articles/x.png',
    'https://dev-to-uploads.s3.us-east-2.amazonaws.com/secrets',
    'https://127.0.0.1/x.png']){
    const f=fixture('ok',url),r=await f.call('devto','upload',articleImage);assert.equal(r.ok,false);assert.equal(f.writes(),1);checks++;
  }
  for(const scenario of ['signed-out','wrong-account']){
    const f=fixture(scenario),r=await f.call('devto','upload',articleImage);assert.equal(r.ok,false);assert.equal(f.writes(),0);checks++;
  }
  for(const scenario of ['changed-after-upload','rejected','upload-timeout','extra-link']){
    const f=fixture(scenario),r=await f.call('devto','upload',articleImage);assert.equal(r.ok,false);assert.equal(f.writes(),1);checks++;
  }
  for(const change of [{account_id:undefined},{mime:'text/plain'},{extension:'svg'},{base64:''}]){
    const f=fixture(),r=await f.call('devto','upload',{...articleImage,...change});assert.equal(r.ok,false);assert.equal(f.writes(),0);checks++;
  }
  const f=fixture();assert.equal((await f.call('devto','save',{title:'No article write'})).ok,false);assert.equal(f.requests.length,0);checks++;
  assert.equal((await fixture().call('csdn','identity')).ok,false);checks++;
  console.log(JSON.stringify({ok:true,checks,coverage:'identity freshness, same account, multipart/CSRF, owned CDN destinations, no article writes, upload error/no retries'}));
})().catch(error=>{console.error(error);process.exitCode=1;});
