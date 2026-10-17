async function(platform, action, arg) {
  if(platform!=='segmentfault' || location.origin!=='https://segmentfault.com')return {ok:false,error:'请在纸飞机内完成思否登录'};
  const numeric=v=>(typeof v!=='number'||Number.isSafeInteger(v)) && /^[1-9][0-9]{0,39}$/.test(String(v||''));
  const state={stage:'session',submitted:false,observed:{}};
  const type=v=>v===null?'null':Array.isArray(v)?'array':typeof v;
  function shape(value) {
    const result=[];
    function visit(v,path,depth) {
      if(result.length>=30)return;
      result.push(path+':'+type(v));
      if(depth>=2 || !v || typeof v!=='object')return;
      for(const key of Object.keys(v).slice(0,30)) {
        if(!/^[a-zA-Z0-9_]{1,32}$/.test(key) || /token|secret|cookie|csrf|session|authorization|password|credential|sign|policy/i.test(key))continue;
        visit(v[key],path?path+'.'+key:key,depth+1);
      }
    }
    visit(value,'$',0);return result;
  }
  function validationReasons(value) {
    // Emit only fixed field/reason labels. Rejected values, URLs and arbitrary
    // platform messages never leave the browser, even when they contain secrets.
    const result=[];
    const fields=['cover','title','content','text','tags','tag','abstract','cate_id','blog_type','copy_code','is_old'];
    const reason=s=>/重复|已存在|duplicate|already.exists/i.test(s)?'duplicate':
      /不能为空|必填|未填写|required|missing|not.empty/i.test(s)?'required':
      /长度|字数|过长|过短|too.long|too.short|max(?:imum)?.*length|min(?:imum)?.*length/i.test(s)?'length':
      /url|链接|地址/i.test(s)?'invalid_url':
      /字符串|数组|类型|must.be.*(?:string|array|number)|invalid.type/i.test(s)?'type':
      /敏感|违规|禁止|不允许|不可发布|不支持发布|cannot.publish/i.test(s)?'restricted':
      /格式|不正确|不合法|错误|invalid/i.test(s)?'format':'invalid';
    const add=(field,message)=>{
      if(typeof message!=='string'||!message)return;
      const item={field,reason:reason(message.slice(0,2000))};
      if(!result.some(v=>v.field===item.field&&v.reason===item.reason))result.push(item);
    };
    for(const field of fields) {
      const v=value?.[field];
      if(typeof v==='string')add(field,v);
      else if(Array.isArray(v))for(const message of v.slice(0,3))add(field,message);
    }
    const messages=Array.isArray(value)?[value[1]]:[value?.message,value?.msg,value?.error,value?.errMsg,value?.data];
    for(const message of messages) {
      if(typeof message!=='string')continue;
      const s=message.slice(0,2000);
      const field=/cover|封面/i.test(s)?'cover':/title|标题/i.test(s)?'title':
        /abstract|摘要/i.test(s)?'abstract':/content|正文|内容|text/i.test(s)?'content':
        /分类|category|cate_id/i.test(s)?'cate_id':/tags?|标签/i.test(s)?'tags':
        /实名|认证|开通博客|绑定手机/i.test(s)?'account':null;
      if(field)add(field,s);
    }
    return result.slice(0,12);
  }
  function observeRejection(value) {
    const validation=validationReasons(value);
    if(validation.length)state.observed.validation=validation;
    return validation;
  }
  function category(value) {
    // Inspect only known error fields; never echo remote text or submitted content.
    const values=Array.isArray(value)?[value[1]]:[value?.message,value?.msg,value?.error,value?.errMsg];
    const s=values.filter(v=>typeof v==='string').join(' ').slice(0,2000);
    if(/csrf|cross.site|request verification|验证请求/i.test(s))return 'csrf';
    if(/captcha|验证码|人机验证/i.test(s))return 'captcha';
    if(/unauthori[sz]ed|未登录|登录失效|请.*登录|token/i.test(s))return 'authentication';
    if(/forbidden|禁言|锁定|权限|无权/i.test(s))return 'permission';
    if(/频繁|too many|rate.limit/i.test(s))return 'rate_limit';
    return observeRejection(value).length?'validation':'response_rejected';
  }
  function problem(kind,message) {const e=Error(message);e.safe=true;e.kind=kind;return e;}
  function failure(e) {
    const kind=e.safe?e.kind:(e.name==='AbortError'||e.name==='TimeoutError'?'timeout':'transport');
    const diagnostic={stage:state.stage,category:kind,submission:state.submitted?'attempted':'not_started',...state.observed};
    const fields=diagnostic.fields?.join(',')||'';
    const validation=(diagnostic.validation||[]).map(v=>v.field+':'+v.reason).join(',');
    const suffix=(validation?' 校验 '+validation:'')+(diagnostic.http?' HTTP '+diagnostic.http:'')+(diagnostic.code!==undefined?' code='+diagnostic.code:'')+(diagnostic.status!==undefined?' status='+diagnostic.status:'')+(fields?' 字段 '+fields:'');
    const summary=(e.safe?e.message:'请求或响应处理未完成')+' ['+state.stage+'/'+kind+']'+suffix;
    return {ok:false,error:summary,uncertain:state.submitted,diagnostic};
  }
  async function jsonResponse(r) {
    state.observed.http=r.status;
    let raw;
    try {raw=await r.text();}catch(e){throw problem('response_read','响应读取中断');}
    let value;
    try {value=JSON.parse(raw);}catch(e) {
      state.observed.response_type=/^\s*</.test(raw)?'html':'non_json';
      state.observed.response_length=raw.length;
      const reason=category({message:raw});
      throw problem(reason==='response_rejected'?(!r.ok?'http':'invalid_response'):reason,'平台未返回JSON（'+state.observed.response_type+'）');
    }
    state.observed.fields=shape(value);
    for(const k of ['code','status']) {
      if((typeof value?.[k]==='number'&&Number.isFinite(value[k])) || (typeof value?.[k]==='string'&&/^-?[0-9]{1,9}$/.test(value[k])))state.observed[k]=value[k];
    }
    if(Array.isArray(value) && Number.isFinite(value[0]))state.observed.code=value[0];
    if(!r.ok) {const reason=category(value);throw problem(reason==='response_rejected'?'http':reason,'平台请求失败');}
    return value;
  }

  async function session() {
    const r=await fetch('/user/settings',{credentials:'include',cache:'no-store',signal:AbortSignal.timeout(30000)});
    if(!r.ok || new URL(r.url,location.origin).origin!==location.origin)throw problem('validation','思否账号核验请求失败');
    const doc=new DOMParser().parseFromString(await r.text(),'text/html');
    const node=doc.querySelector('#__NEXT_DATA__');
    if(!node)throw problem('validation','思否登录页面结构已变化，请检查平台窗口');
    let stateData;
    try {stateData=JSON.parse(node.textContent);}catch(e){throw problem('page_structure','思否身份页面JSON无效');}
    const g=stateData?.props?.pageProps?.initialState?.global;
    if(g?.sessionInfo?.login===false)return {account:null};
    const u=g?.sessionUser?.user;
    if(!g?.sessionInfo?.login || !numeric(u?.id))throw problem('validation','未能核实思否账号');
    // Official browser Request.send uses the current same-origin PHPSESSID.
    // Keep it solely in this page and send only to the relative /gateway endpoint.
    let currentToken='';
    const cookieEntry=document.cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('PHPSESSID='));
    if(cookieEntry) {
      try {currentToken=decodeURIComponent(cookieEntry.slice('PHPSESSID='.length));}
      catch(e) {throw problem('authentication','思否当前登录校验信息无效');}
    }
    const rawToken=currentToken||g.serverData?.Token||g.sessionInfo?.key;
    state.observed.auth_source=currentToken?'same-origin-session':g.serverData?.Token?'serverData.Token':'sessionInfo.key';
    state.observed.auth_page='/user/settings';
    state.observed.auth_segments=typeof rawToken==='string'?rawToken.split('~').length:0;
    const token=typeof rawToken==='string' && rawToken.includes('~')?rawToken.split('~')[1]:rawToken;
    if(typeof token!=='string' || !token || token.length>1000)throw problem('validation','思否登录校验信息缺失');
    return {account:{id:String(u.id),name:String(u.name||u.slug||u.id)},token};
  }
  async function request(path,token,options={}) {
    state.stage=path==='/draft'?'draft-submit':'upload-request';
    if(path==='/draft')state.submitted=true;
    const r=await fetch('/gateway'+path,{credentials:'include',redirect:'error',signal:AbortSignal.timeout(30000),...options,
      headers:{token,authorization:'Bearer '+token,...options.headers}});
    const v=await jsonResponse(r);
    if(Array.isArray(v)) {if(v[0]!==0)throw problem(category(v),'思否拒绝请求（返回码 '+String(v[0]).replace(/[^0-9-]/g,'').slice(0,10)+'）');return v[1];}
    if(!v || v.error || v.isError)throw problem(category(v),'思否未返回有效结果');
    return v;
  }
  function imageUrl(value) {
    if(typeof value!=='string')throw problem('validation','思否图片地址无效');
    const u=new URL(value,location.origin);
    if(u.protocol!=='https:' || u.port || u.username || u.password ||
       !(u.hostname==='segmentfault.com'||u.hostname.endsWith('.segmentfault.com')))throw problem('validation','思否图片地址未能核实');
    return u.href;
  }
  function coverReference(value) {
    if(!value)return '';
    const u=new URL(imageUrl(value));
    // The official Write component stores upload.url (/img/...), while
    // upload.result is the CDN display URL. Only the original cover reference
    // is accepted by /gateway/draft; never fabricate it from a CDN URL.
    if(u.origin!==location.origin || u.search || u.hash || !/^\/img\/[A-Za-z0-9_-]+$/.test(u.pathname))
      throw problem('validation','思否封面缺少平台原始图片引用，请重新选择图片后创建新任务');
    return u.pathname;
  }
  async function readDraft(id, article, auth) {
    const url='https://segmentfault.com/write?draftId='+id;
    try {
      // Use the official editor's server state; raw gateway GETs require platform signatures.
      const r=await fetch('/write?draftId='+id,{credentials:'include',cache:'no-store',signal:AbortSignal.timeout(30000)});
      if(!r.ok || new URL(r.url,location.origin).origin!==location.origin)throw problem('validation','思否原草稿页面读取失败');
      const doc=new DOMParser().parseFromString(await r.text(),'text/html');
      const node=doc.querySelector('#__NEXT_DATA__');
      if(!node)throw problem('validation','思否原草稿页面结构已变化');
      let parsed;
      try {parsed=JSON.parse(node.textContent);}catch(e){throw problem('page_structure','思否草稿页面JSON无效');}
      const state=parsed?.props?.pageProps?.initialState;
      const owner=state?.global?.sessionUser?.user;
      const draft=state?.editor?.detail?.draft;
      if(state?.global?.sessionInfo?.login!==true || String(owner?.id)!==auth.account.id)throw problem('validation','思否原草稿所属账号未能核实');
      const cover=coverReference(article.cover);
      if(String(draft?.id)!==id || draft.title!==article.title || draft.text!==article.markdown ||
         (draft.object_id && String(draft.object_id)!=='0') || (draft.type_str && draft.type_str!=='article') ||
         (cover && coverReference(typeof draft.cover==='string'?draft.cover.replace(/_cover$/,''):'')!==cover) || (await session()).account?.id!==auth.account.id)throw problem('validation','原草稿标题、正文或图片回读不一致');
      return {id,url};
    } catch(e) {return {id,url,incomplete:true,reason:e.message};}
  }
  try {
    const auth=await session();
    if(action==='identity')return {ok:true,data:auth.account};
    if(action==='diagnose')return {ok:true,data:{authenticated:!!auth.account,...state.observed}};
    if(!auth.account)throw problem('validation','请先登录思否');
    if(action==='upload') {
      state.stage='upload-preflight';
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw problem('validation','图片格式无效');
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw problem('validation','图片为空或超过5MB');
      const data=new FormData();data.append('image',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);
      const v=await request('/image',auth.token,{method:'POST',body:data});
      return {ok:true,data:imageUrl(typeof v==='string'?v:v.url||v.result)};
    }
    if(action==='draft-read') {
      state.stage='draft-read';
      const id=String(arg?.draft_id||'');
      if(!numeric(id) || !arg?.title?.trim() || !arg?.markdown?.trim())throw problem('validation','缺少原草稿编号或任务正文，未执行核对');
      if(auth.account.id!==arg.account_id)throw problem('validation','思否账号已变化，请重新核对');
      return {ok:true,data:await readDraft(id,arg,auth)};
    }
    if(action==='save') {
      state.stage='preflight';
      if(auth.account.id!==arg?.account_id)throw problem('validation','思否账号已变化，请重新核对');
      if(!arg.title?.trim() || !arg.markdown?.trim() || arg.markdown.includes('/media/'))throw problem('validation','标题、正文或图片未准备好');
      const cover=coverReference(arg.cover);
      const created=await request('/draft',auth.token,{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({type:'article',object_id:'',title:arg.title,text:arg.markdown,tags:[],cover})});
      state.stage='draft-receipt';
      if(!numeric(created?.id))throw problem('missing_receipt','未收到有效草稿编号，请核对草稿箱，不要重复发送');
      return {ok:true,data:await readDraft(String(created.id),arg,auth)};
    }
    return {ok:false,error:'思否此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return failure(e);}
}
