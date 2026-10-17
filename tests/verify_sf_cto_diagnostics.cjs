// Isolated protocol checks: no real credentials, network, or drafts.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const title='diagnostic fixture',markdown='fixture body',secret='DO_NOT_OUTPUT_SECRET';
let checks=0;
function check(v,message){assert.ok(v,message);checks++;}
async function run(platform,scenario='ok',options={}) {
  const sf=platform==='segmentfault',origin=sf?'https://segmentfault.com':'https://blog.51cto.com';
  let writes=0,saved=null,headers=null;
  const calls=[];
  const freshForm={is_old:'0',did:'',blog_id:'',title:'',content:'',abstract:'',pid:'',cate_id:'',custom_id:0,tag:'',blog_type:1,copy_code:2,is_hide:0,top_time:'',is_comment:'',banner_type:0,img_urls:[],import_type:''};
  const cookie=options.cookie===undefined?'PHPSESSID=prefix~'+secret:options.cookie;
  class Parser {
    parseFromString(text) {
      if(sf)return {querySelector:selector=>selector==='#__NEXT_DATA__'?{textContent:text}:null};
      const isDraft=JSON.parse(text)?.fixturePage==='draft';
      const form=isDraft?{...saved,did:scenario==='wrong-read-id'?'999':'123',blog_id:scenario==='published-read'?'555':'',img_urls:saved?.['img_urls[]']?[saved['img_urls[]']]:[]}:freshForm;
      const supplied=isDraft?options.readForm:options.initialForm;
      const initial=supplied===undefined?'var submitForm = '+JSON.stringify(form)+';':supplied;
      const blogInputs=isDraft?(options.blogInputs||[]).map(v=>({id:'blog_id',name:'blog_id',type:v.type||'hidden',value:v.value})):[];
      return {querySelectorAll:selector=>selector==='script:not([src])'?[{textContent:initial}]:
        ['input#blog_id','input[id],input[name]'].includes(selector)?blogInputs:[],querySelector:selector=>{ 
        if(selector==='input#is_old')return {value:options.mode===undefined?'0':options.mode};
        if(selector==='input#csrf'&&options.inputCsrf)return {value:'input-'+secret,getAttribute:()=>options.inputCsrf};
        if(selector==='li.more.user a[href]')return {querySelector:()=>({}),getAttribute:()=>'/456'};
        if(selector==='meta[name="csrf-token"]')return {content:secret};
        if(selector==='meta[name="csrf-param"]')return options.csrfParam?{content:options.csrfParam}:null;
        if(selector==='input[name="title"]')return {value:saved?.title};
        if(selector==='textarea[name="content"]')return {value:saved?.content};
        return null;
      }};
    }
  }
  const reply=(value,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(value)});
  const html=(value,url)=>({ok:true,status:200,url:origin+url,text:async()=>JSON.stringify(value)});
  const globalState={sessionInfo:{login:true,key:'fallback-'+secret},serverData:{Token:'stale-'+secret},sessionUser:{user:{id:'456',name:'Fixture'}}};
  const fetch=async (url,opt={})=>{
    calls.push({url,method:opt.method||'GET'});
    if(url==='/user/settings'||url==='/blogger/publish') {
      if(scenario==='session-network')throw new Error('fixture network '+secret);
      return html(sf?{props:{pageProps:{initialState:{global:globalState}}}}:{fixturePage:'new'},url);
    }
    if(url==='/gateway/draft'||url==='/blogger/draft') {
      writes++;headers=opt.headers;saved=sf?JSON.parse(opt.body):Object.fromEntries(new URLSearchParams(opt.body));
      if(scenario==='zero-chinese-data')return reply({status:0,msg:'error',data:'操作未完成，请稍后再试。'});
      if(scenario==='zero-id-string')return reply({status:0,msg:'操作失败',data:'123'});
      if(scenario==='chinese-error')return reply({status:0,msg:'操作未完成，请稍后再试。',data:secret});
      if(scenario==='chinese-secret')return reply({status:0,msg:'签名：敏感凭证',data:secret});
      if(scenario==='url-error')return reply({status:0,msg:'错误：https://secret.example/'+secret,data:secret});
      if(scenario==='title-echo')return reply({status:0,msg:'中文标题已存在',data:secret});
      if(scenario==='cover-error')return reply({cover:'Invalid URL '+secret},400);
      if(scenario==='length-error')return reply({status:0,msg:'摘要字数过长 '+secret,data:secret});
      if(scenario==='duplicate-error')return reply({status:0,msg:'标题已存在 '+secret,data:secret});
      if(scenario==='network')throw new Error('fixture unknown '+secret);
      if(scenario==='html')return {ok:false,status:403,text:async()=>'<html>'+secret+'</html>'};
      if(scenario==='unauthorized')return {ok:true,status:200,text:async()=>'Unauthorized '+secret};
      if(scenario==='reject')return reply(sf?[1,'unauthorized '+secret]:{status:0,msg:'CSRF invalid '+secret,token:secret});
      if(scenario==='missing-id')return reply(sf?{title:secret,content:secret,token:secret}:{status:1,data:{content:secret,token:secret}});
      if(scenario==='unsafe-id')return reply(sf?{id:9007199254740994}:{status:1,data:{did:9007199254740994}});
      return reply(sf?{id:'123'}:{status:1,data:{did:'123'}});
    }
    if(url==='/gateway/image'){writes++;return reply({url:'/img/native-image',result:'https://image-static.segmentfault.com/display-image'});}
    if(url==='/write?draftId=123')return html({props:{pageProps:{initialState:{global:globalState,editor:{detail:{draft:{...saved,id:'123'}}}}}}},url);
    if(url==='/blogger/draft/123')return html({fixturePage:'draft'},url);
    throw new Error('unexpected endpoint '+url);
  };
  const script=fs.readFileSync(path.join(__dirname,'../web/',sf?'segmentfault_transport.js':'cto51_transport.js'),'utf8');
  const fn=vm.runInNewContext('('+script+')',{location:{origin},document:{cookie},DOMParser:Parser,fetch,AbortSignal,URL,URLSearchParams,Blob,FormData,Uint8Array,atob});
  const arg={account_id:'456',title,markdown,cover:''};
  const result=await fn(platform,options.action||'save',{...arg,...options.arg});
  check(!JSON.stringify(result).includes(secret),'credentials and remote text never returned');
  return {result,writes,saved,headers,calls};
}
(async()=>{
  for(const platform of ['segmentfault','51cto']) {
    let x=await run(platform);
    check(x.result.ok&&!x.result.data.incomplete,'exact readback succeeds');
    check(x.writes===1,'one draft write');
    check(x.calls.every(c=>!c.url.includes('article')&&!c.url.includes('/submit')),'no publication endpoint');
    x=await run(platform,'session-network');
    check(!x.result.ok&&x.result.uncertain===false&&x.writes===0,'session failure before submission');
    check(x.result.diagnostic.stage==='session','session stage');
    x=await run(platform,'ok',{arg:{account_id:'other'}});
    check(!x.result.ok&&!x.result.uncertain&&x.writes===0,'changed account blocked before write');
    for(const scenario of ['network','html','unauthorized','reject','missing-id','unsafe-id']) {
      x=await run(platform,scenario);
      check(!x.result.ok&&x.result.uncertain===true&&x.writes===1,'unknown save never retried: '+scenario);
      check(x.result.diagnostic.submission==='attempted','submitted state retained');
      check(x.result.error.includes('draft-'),'save stage visible in error');
      if(scenario==='html')check(x.result.diagnostic.http===403&&x.result.diagnostic.response_type==='html','HTTP / HTML classified');
      if(scenario==='unauthorized')check(x.result.diagnostic.category==='authentication','non-JSON authorization classified');
      if(scenario==='missing-id'||scenario==='unsafe-id')check(x.result.diagnostic.category==='missing_receipt','missing or unsafe ID rejected');
      if(scenario==='reject')check(x.result.diagnostic.category===(platform==='segmentfault'?'authentication':'csrf'),'rejection reason classified safely');
    }
    x=await run(platform,'ok',{action:'diagnose'});
    check(x.result.ok&&x.writes===0&&x.calls.every(c=>c.method==='GET'),'diagnose only reads');
    if(platform==='segmentfault')check(x.result.data.auth_source==='same-origin-session','current browser session is preferred');
  }
  let x=await run('segmentfault');
  check(x.headers.token===secret&&x.headers.authorization==='Bearer '+secret,'official split browser session used only in same-origin headers');
  check(!JSON.stringify(x.saved).includes(secret),'session not placed in draft payload');
  x=await run('segmentfault','ok',{cookie:''});
  check(x.headers.token==='stale-'+secret,'SSR fallback when cookie is unavailable');
  x=await run('51cto','ok',{csrfParam:'_csrf-frontend'});
  check(x.saved['_csrf-frontend']===secret&&!('_csrf' in x.saved),'honor exact CSRF parameter from official page');
  x=await run('51cto','ok',{csrfParam:'bad&parameter'});
  check(!x.result.ok&&!x.result.uncertain&&x.writes===0,'invalid CSRF parameter blocks before save');
  x=await run('segmentfault','cover-error');
  check(x.result.diagnostic.category==='validation'&&x.result.error.includes('cover:invalid_url'),'cover validation reason is visible without rejected value');
  check(x.writes===1&&x.result.uncertain,'rejected save is not replayed');
  x=await run('51cto','length-error');
  check(x.result.error.includes('abstract:length'),'summary length rejection is identified');
  x=await run('51cto','duplicate-error');
  check(x.result.error.includes('title:duplicate'),'duplicate title rejection is identified');
  x=await run('segmentfault','ok',{action:'upload',arg:{base64:'YWJj',extension:'png',mime:'image/png'}});
  check(x.result.data==='https://segmentfault.com/img/native-image','official upload.url retained for cover');
  x=await run('segmentfault','ok',{arg:{cover:'https://segmentfault.com/img/native-image'}});
  check(x.saved.cover==='/img/native-image'&&x.result.ok&&!x.result.data.incomplete,'native relative cover submitted and read back exactly');
  x=await run('segmentfault','ok',{arg:{cover:'https://image-static.segmentfault.com/display-image'}});
  check(!x.result.ok&&!x.result.uncertain&&x.writes===0,'CDN cover is not guessed or silently dropped');
  x=await run('51cto','chinese-error');
  check(x.result.diagnostic.safe_message==='操作未完成，请稍后再试。'&&x.result.error.includes('操作未完成，请稍后再试。'),'bounded Chinese rejection is retained');
  for(const scenario of ['chinese-secret','url-error']) {
    x=await run('51cto',scenario);
    check(!x.result.diagnostic.safe_message,'secrets or URLs are never returned as safe messages');
  }
  x=await run('51cto','title-echo',{arg:{title:'中文标题'}});
  check(!x.result.diagnostic.safe_message,'submitted article title is not echoed');
  x=await run('51cto','zero-chinese-data');
  check(x.result.diagnostic.safe_message==='操作未完成，请稍后再试。'&&!x.result.ok&&x.writes===1,'bounded Chinese error data is available only for status zero');
  x=await run('51cto','zero-id-string');
  check(!x.result.ok&&x.result.uncertain&&x.writes===1&&!x.calls.some(c=>c.url.includes('/draft/123')),'official status zero never treated as a saved ID or replayed');
  x=await run('51cto','ok',{action:'diagnose',initialForm:'var submitForm = '+JSON.stringify({title:secret,content:'braces } { " '+secret,_csrf:secret,access_token:secret,is_old:'0',blog_type:1,copy_code:0,is_hide:0,top_time:1777777777,is_comment:'',banner_type:4,import_type:-1,did:'',blog_id:'123',pid:0,cate_id:'0',custom_id:null,img_urls:[],unexpected:secret})+'; alert("DO_NOT_EXECUTE")'});
  check(x.result.data.submit_form_parse==='json','only bounded inline JSON is parsed');
  check(x.result.data.submit_form_fields.some(f=>f.name==='content'&&f.type==='string'),'article only has type metadata');
  check(!x.result.data.submit_form_fields.some(f=>/csrf|token/.test(f.name)),'auth fields excluded from initialization metadata');
  check(x.result.data.submit_form_options.is_old==='0'&&x.result.data.submit_form_options.banner_type===4&&x.result.data.submit_form_options.import_type===-1,'fixed short option defaults are readable');
  check(!('top_time' in x.result.data.submit_form_options)&&x.result.data.submit_form_options.did_blank&&x.result.data.submit_form_options.blog_id_blank===false,'timestamps and actual IDs withheld');
  check(x.writes===0&&x.calls.every(c=>c.method==='GET'),'form metadata reads never write');
  x=await run('51cto','ok',{action:'diagnose',initialForm:'var submitForm = {is_old: dangerous()};'});
  check(x.result.data.submit_form_parse==='unrecognized'&&!x.result.data.submit_form_options,'non-JSON page expression is never evaluated');
  for(const initialForm of ['', 'var submitForm = {is_old: dangerous()};','var submitForm = '+JSON.stringify({unknown:'value'})]) {
    x=await run('51cto','ok',{initialForm});
    check(!x.result.ok&&!x.result.uncertain&&x.writes===0,'unrecognized official form prevents any draft write');
  }
  for(const mode of ['1','2','evil',undefined]) {
    x=await run('51cto','ok',{mode:mode===undefined?null:mode});
    check(!x.result.ok&&!x.result.uncertain&&x.writes===0,'missing or non-Markdown official editor prevents draft writes');
  }
  x=await run('51cto','ok');
  check(x.saved.copy_code==='2'&&x.saved.is_hide==='0'&&x.saved.import_type==='-1','fresh official options and import empty handling preserved');
  x=await run('51cto','ok',{inputCsrf:'_csrf-editor'});
  check(x.saved['_csrf-editor']==='input-'+secret&&!('_csrf' in x.saved),'official editor hidden CSRF used only in request');
  x=await run('51cto','ok',{initialForm:'var submitForm = '+JSON.stringify({is_old:0,did:'456',blog_id:789,pid:5,cate_id:6,custom_id:9,subjectId:11,work_id:44,class_id:55,banner_type:0,img_urls:[]})+';'});
  check(x.saved.did===''&&x.saved.blog_id===''&&x.saved.pid==='0'&&x.saved.subjectId==='0'&&x.saved.work_id==='0','existing IDs and contexts reset before one new private draft');
  x=await run('51cto','ok',{arg:{cover:'https://s2.51cto.com/cover.png'}});
  check(x.result.ok&&!x.result.data.incomplete&&x.saved['img_urls[]']==='https://s2.51cto.com/cover.png'&&x.saved.banner_type==='1','official single-cover array serialized and read back');
  for(const scenario of ['wrong-read-id','published-read']) {
    x=await run('51cto',scenario);
    check(x.result.ok&&x.result.data.incomplete&&x.writes===1,'readback ID or published object mismatch stays uncertain without replay');
  }
  const literal="/* data, no execution */ var submitForm = {\n is_old:'0', did:'', blog_id:'', title:'ignore } \\\" quote', content:'ignore /* braces } */', abstract:'', copy_code:2, is_hide:0, is_comment:'', top_time:'', banner_type:0, img_urls:[/* empty */], import_type:-1, // trailing comment\n};";
  x=await run('51cto','ok',{initialForm:literal});
  check(x.result.ok&&!x.result.data.incomplete&&x.writes===1&&x.saved.copy_code==='2','single quotes, bare keys, comments and trailing comma produce exact private draft');
  x=await run('51cto','ok',{action:'diagnose',initialForm:literal});
  check(x.result.data.submit_form_parse==='js_literal'&&Object.values(x.result.data.submit_form_syntax).every(Boolean),'literal syntax metadata contains booleans only');
  x=await run('51cto','ok',{readForm:"let submitForm = {did:'123',blog_id:'',is_old:'0',title:'diagnostic\\x20fixture',content:'fixture\\u0020body',banner_type:0,img_urls:[],};"});
  check(x.result.ok&&!x.result.data.incomplete,'JS hexadecimal and Unicode string escapes are decoded exactly for readback');
  x=await run('51cto','ok',{initialForm:"const submitForm={is_old:'0',title:'var submitForm = {}',banner_type:0,img_urls:[]};"});
  check(x.result.ok&&!x.result.data.incomplete,'declaration-looking article strings cannot become templates');
  const officialLiteral="var submitForm={is_old:'0',title:null,content:'',banner_type:'0'.length == 0 ? '0' : '0',blog_type:''?'':'1',copy_code:'1'?'1':1,blog_id:''||'',did:'',img_urls:[],import_type:'',raffle:''}\nvar nextOfficialValue='ignored';";
  x=await run('51cto','ok',{initialForm:officialLiteral});
  check(x.result.ok&&!x.result.data.incomplete&&x.writes===1,'official constant defaults and ASI declaration parse without execution');
  check(x.saved.banner_type==='0'&&x.saved.blog_type==='1'&&x.saved.copy_code==='1'&&x.saved.blog_id===''&&x.saved.raffle==='','official conditional defaults preserve exact form values');
  x=await run('51cto','ok',{action:'diagnose',initialForm:officialLiteral});
  check(x.result.data.submit_form_syntax.constant_conditionals&&x.result.data.draft_preflight.ready&&x.result.data.draft_preflight.existing_ids_cleared&&x.writes===0,'real form syntax passes read-only preflight');
  for(const [expression,expected] of [["'a'.length===1?'1':'2'",'1'],["''.length!=0?'1':'2'",'2'],["false||true&&'1'",'1'],["'1'&&'2'||'3'",'2']]) {
    x=await run('51cto','ok',{initialForm:officialLiteral.replace("copy_code:'1'?'1':1",'copy_code:'+expression)});
    check(x.result.ok&&!x.result.data.incomplete&&x.saved.copy_code===expected,'constant grammar retains precedence and exact value');
  }
  const privateReadForm=blogId=>'var submitForm='+JSON.stringify({did:'123',blog_id:blogId,is_old:'0',title,content:markdown,banner_type:0,img_urls:[]})+';';
  for(const test of [
    {name:'official draft repeats did with empty hidden published ID',blogId:'123',inputs:[{value:''}],pass:true},
    {name:'old template keeps blank ID without hidden input',blogId:'',inputs:[],pass:true},
    {name:'blank template with empty hidden published ID',blogId:'',inputs:[{value:''}],pass:true},
    {name:'published hidden ID',blogId:'123',inputs:[{value:'777'}]},
    {name:'published hidden ID with blank template',blogId:'',inputs:[{value:'777'}]},
    {name:'duplicated hidden ID',blogId:'123',inputs:[{value:''},{value:''}]},
    {name:'other template ID',blogId:'999',inputs:[{value:''}]},
    {name:'repeated did without hidden evidence',blogId:'123',inputs:[]},
    {name:'text input cannot prove private draft',blogId:'123',inputs:[{value:'',type:'text'}]},
    {name:'text input invalid even with blank template',blogId:'',inputs:[{value:'',type:'text'}]}
  ]) {
    x=await run('51cto','ok',{readForm:privateReadForm(test.blogId),blogInputs:test.inputs});
    check(x.result.ok&&Boolean(!x.result.data.incomplete)===Boolean(test.pass),'private draft evidence: '+test.name);
    check(x.writes===1,'readback mismatch does not replay save: '+test.name);
  }
  const invalidLiterals=[
    "var submitForm={is_old:dangerous()};", "var submitForm={is_old:false?dangerous():0};",
    "var submitForm={is_old:true?0:dangerous()};", "var submitForm={is_old:{}.length};",
    "var submitForm={is_old:{}?0:1};", "var submitForm={is_old:'0'.getLength()};", "var submitForm={is_old:globalValue};",
    "var submitForm={get is_old(){return 0}};", "var submitForm={[dangerous()]:0};",
    "var submitForm={...other};", "var submitForm={is_old};",
    "var submitForm={is_old:`0`};", "var submitForm={is_old:0,is_old:1};",
    "var submitForm={__proto__:{is_old:0}};", "var submitForm={constructor:0};",
    "var submitForm={is_old:0} || dangerous();", "var submitForm={is_old:0}.value;",
    "var submitForm={is_old:0}; var submitForm={is_old:1};",
    "var submitForm={did:9007199254740993};", "var submitForm={title:'unterminated};",
    "var submitForm={img_urls:[,]};", "var submitForm={title:'bad\\uZZZZ'};",
    "var submitForm={title:'bad\\8'};", "var submitForm={title:'bad\\u{110000}'};"
  ];
  for(const initialForm of invalidLiterals) {
    x=await run('51cto','ok',{initialForm});
    check(!x.result.ok&&!x.result.uncertain&&x.writes===0,'expression or ambiguous literal never executes or writes');
    check(x.result.diagnostic.submit_form_parse==='unrecognized'&&/^[a-z_]+$/.test(x.result.diagnostic.submit_form_error),'parse diagnostics expose only fixed failure categories');
  }
  x=await run('51cto','ok',{initialForm:"var submitForm={is_old:0,title:'DO_NOT_OUTPUT_SECRET',newUnknownField:1};"});
  check(!x.result.ok&&!x.result.uncertain&&x.writes===0&&x.result.diagnostic.submit_form_parse==='js_literal','unknown form fields still block after literal parsing');
  console.log(JSON.stringify({ok:true,checks}));
})().catch(e=>{console.error(e);process.exitCode=1;});
