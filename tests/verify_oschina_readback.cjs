// Pure protocol fixtures: only GET is permitted; no real account or network.
const fs=require('fs'),assert=require('assert'),path=require('path');
const script=eval('('+fs.readFileSync(path.join(__dirname,'../web/oschina_transport.js'),'utf8')+')');
global.location={origin:'https://my.oschina.net'};
const article={account_id:'456',draft_id:'123',title:'fixture',markdown:'body ![img](https://static.oschina.net/img.png)',cover:''};
async function run(scenario) {
  const calls=[];let identities=0,listRead=false;
  global.fetch=async(url,options)=>{
    assert(!options.method || options.method==='GET');assert.equal(options.cache,'no-store');calls.push(url);
    const response=result=>({ok:true,status:200,json:async()=>({code:200,success:true,result})});
    if(url.endsWith('/user/myDetails')) {identities++;return response({userId:scenario==='account-change'&&listRead?999:456,userVo:{name:'Fixture'}});}
    if(url.endsWith('/edit/detail?id=123')) {
      return response({draft:123,title:article.title,content:article.markdown,contentType:1,user:null,
        ...(scenario==='direct-owner'?{space:{id:456}}:{}),
        ...(scenario==='detail-conflict'?{space:999}:{}),
        ...(scenario==='detail-owner-invalid'?{space:{unknown:456}}:{}),
        ...(scenario==='body-mismatch'?{content:'changed'}:{}),
        ...(scenario==='title-mismatch'?{title:'changed'}:{}),
        ...(scenario==='type-mismatch'?{contentType:2}:{})});
    }
    if(url.includes('/api/draft/list?')) {
      listRead=true;const page=Number(new URL(url).searchParams.get('pageNum'));
      if(scenario==='list-error')throw Error('unavailable');
      const own={id:123,space:456};
      if(scenario==='array')return response([own]);
      if(scenario==='no-list-owner')return response([{id:123}]);
      if(scenario==='records')return response({records:[own],size:10,current:1,pages:1});
      if(scenario==='list-shape')return response({list:[own],size:10,page:1,pages:1});
      if(scenario==='page-two')return response({records:page===1?Array.from({length:10},(_,i)=>({id:1000+i,space:456})):[own],size:10,current:page,pages:2});
      if(scenario==='missing')return response([]);
      if(scenario==='list-invalid')return response({items:[own]});
      if(scenario==='list-conflict')return response([{id:123,space:999}]);
      if(scenario==='list-owner-invalid')return response([{id:123,space:{unknown:456}}]);
      if(scenario==='duplicate')return response([own,own]);
      if(scenario==='repeated-page')return response(Array.from({length:10},(_,i)=>({id:1000+i,space:456})));
      if(scenario==='bounded-pages')return response(Array.from({length:10},(_,i)=>({id:1000+10*page+i,space:456})));
      return response([own]);
    }
    throw Error('unexpected endpoint '+url);
  };
  const outcome=await script('oschina','draft-read',article);
  assert.equal(outcome.ok,true,scenario);
  return {outcome:outcome.data,calls};
}
(async()=>{
  let count=0;
  for(const scenario of ['direct-owner','array','records','list-shape','page-two','no-list-owner']) {
    const {outcome,calls}=await run(scenario);assert.deepEqual(outcome,{id:'123',url:'https://my.oschina.net/u/456/blog/ai-write/draft/123'},scenario);
    if(scenario==='direct-owner')assert(!calls.some(url=>url.includes('/draft/list')));count++;
  }
  for(const scenario of ['detail-conflict','detail-owner-invalid','missing','list-invalid','list-conflict','list-owner-invalid','duplicate','repeated-page','bounded-pages','list-error','account-change','body-mismatch','title-mismatch','type-mismatch']) {
    const {outcome,calls}=await run(scenario);assert.equal(outcome.incomplete,true,scenario);
    if(scenario.startsWith('detail-'))assert(!calls.some(url=>url.includes('/draft/list')));
    if(scenario==='repeated-page')assert.equal(calls.filter(url=>url.includes('/draft/list')).length,2);
    if(scenario==='bounded-pages')assert.equal(calls.filter(url=>url.includes('/draft/list')).length,10);
    assert(!JSON.stringify(outcome.observed).includes(article.markdown));count++;
  }
  console.log(JSON.stringify({ok:true,checks:count,writes:0}));
})().catch(error=>{console.error(error);process.exitCode=1});
