async function(platform, action, arg) {
  if(platform!=='zhihu' || location.origin!=='https://zhuanlan.zhihu.com')return {ok:false,error:'请在纸飞机内完成知乎登录'};
  const fieldType=value=>value===null?'null':Array.isArray(value)?'array':typeof value;
  const failure=(message,httpStatus)=>{const e=Error(message);e.safe=true;if(Number.isInteger(httpStatus))e.http_status=httpStatus;return e;};
  const numeric=value=>typeof value==='string' && /^[1-9][0-9]{0,39}$/.test(value);
  const exactId=value=>typeof value==='number'&&!Number.isSafeInteger(value)?'':typeof value==='string'||typeof value==='number'?String(value):'';
  const objectId=value=>exactId(value?.id??value?.slug);
  const authorId=value=>exactId(value?.author?.id??value?.author?.hash);
  const accountId=value=>typeof value==='string' && /^[a-zA-Z0-9_-]{1,200}$/.test(value);
  function parseResponse(text) {
    return JSON.parse(text.replace(/"(?:[^"\\]|\\.)*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/g,token=>
      token[0]==='"'||/[.eE]/.test(token)||Number.isSafeInteger(Number(token))?token:JSON.stringify(token)));
  }
  function shape(value,depth=0) {
    const out={type:fieldType(value)};
    if(value && typeof value==='object' && !Array.isArray(value) && depth<2) {
      out.fields={};for(const key of Object.keys(value).filter(k=>/^[a-zA-Z][a-zA-Z0-9_]{0,60}$/.test(k) && !/(?:token|authorization|auth_key|cookie|secret|password|csrf)/i.test(k)).slice(0,50))out.fields[key]=shape(value[key],depth+1);
    } else if(Array.isArray(value))out.count=value.length;
    return out;
  }
  async function request(path,method='GET',data,observed) {
    const url=path.startsWith('/')?'https://zhuanlan.zhihu.com'+path:path;
    const step={stage:observed?.stage||action,method,path:new URL(url).pathname};
    if(observed)(observed.requests||(observed.requests=[])).push(step);
    const headers={'x-requested-with':'fetch'};
    if(method!=='GET') {
      const xsrf=document.cookie.match(/(?:^|;\s*)_xsrf=([^;]+)/);
      if(xsrf)headers['x-xsrftoken']=decodeURIComponent(xsrf[1]);
    }
    if(data!==undefined)headers['content-type']='application/json';
    let r;
    try{r=await fetch(url,{method,headers,credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(20000),...(data===undefined?{}:{body:JSON.stringify(data)})});}
    catch{throw failure('知乎请求未完成，请核对登录或平台服务状态');}
    step.http_status=r.status;
    if(!r.ok)throw failure('知乎请求失败（HTTP '+r.status+'）',r.status);
    if(r.status===204)return null;
    let text;try{text=await r.text();}catch{throw failure('知乎响应读取未完成',r.status);}
    if(!text)return null;
    let value;try{value=parseResponse(text);}catch{throw failure('知乎未返回有效响应',r.status);}
    step.response_type=fieldType(value);if(observed)step.response=shape(value);
    if(Number.isInteger(value?.error?.code))step.platform_code=value.error.code;
    if(value?.error)throw failure('知乎拒绝请求，请查看平台提示',r.status);
    return value;
  }
  async function identity(observed) {
    const u=await request('/api/me','GET',undefined,observed),id=exactId(u?.id??u?.hash);
    return accountId(id)&&typeof u?.name==='string'&&u.name?{id,name:u.name}:null;
  }
  const privateImageHost='pic-private.zhihu.com';
  const publicImageHost=/^pic(?:[1-4ab]|x)\.zhimg\.com$/;
  const publicImageKeys=new Set(['source','width','height','quality','format','imageView2','imageMogr2','x-oss-process','protocol','no_change']);
  const privateImageKeys=new Set(['animatedImageAutoPlay','animatedImagePlayCount','auth_key','expiration','incremental','overTime','precoder','protocol','retryCount','sampling','sceneCode','source','no_change','draft_token']);
  function imageUrl(value,allowExpired=false) {
    const invalid=()=>{throw failure('知乎图片地址无效');};
    if(typeof value!=='string' || value.length>4096 || /[\s\\\x00-\x1f\x7f]/.test(value))invalid();
    let u;try{u=new URL(value);}catch{invalid();}
    const privateImage=u.hostname===privateImageHost;
    if(u.protocol!=='https:' || u.port || u.username || u.password || u.hash || !(privateImage || publicImageHost.test(u.hostname)))invalid();
    const rawPath=value.match(/^https:\/\/[^/?#]+([^?#]*)/i)?.[1]||'';
    const allowedPath=privateImage?/^\/[a-zA-Z0-9_./~!$&'()*+,;=:@%\-]+$/:/^\/[a-zA-Z0-9_./~\-]+$/;
    if(rawPath.length>2048 || !allowedPath.test(rawPath) || /%(?![a-f0-9]{2})/i.test(rawPath))invalid();
    let decoded;try{decoded=decodeURIComponent(rawPath);}catch{invalid();}
    const parts=decoded.split('/').slice(1);
    if(/[\s\\\x00-\x1f\x7f?#]/.test(decoded) || parts.length<1 || parts.length>5 || parts.some(v=>!v || v==='.' || v==='..'))invalid();
    const entries=[...u.searchParams],keys=privateImage?privateImageKeys:publicImageKeys;
    if(entries.length>24 || new Set(entries.map(([k])=>k)).size!==entries.length || entries.some(([k,v])=>!keys.has(k) || !/^[a-zA-Z0-9_.,~:/!@%+=()\-]{0,1024}$/.test(v)))invalid();
    if(u.searchParams.has('protocol') && u.searchParams.get('protocol')!=='v2' || u.searchParams.has('no_change') && u.searchParams.get('no_change')!=='1')invalid();
    if(privateImage) {
      const expiration=u.searchParams.get('expiration');
      if(u.searchParams.get('protocol')!=='v2' || !u.searchParams.get('auth_key') || !/^(?:-1|[0-9]{1,12})$/.test(expiration||''))invalid();
      if(u.searchParams.has('draft_token') && !numeric(u.searchParams.get('draft_token')))invalid();
      if(!allowExpired && Number(expiration)>=0 && Date.now()/1000>=Number(expiration))throw failure('知乎图片引用已过期，正文未提交，请重新核对原图片');
    }
    return u.href;
  }
  function bindImage(value,draftId,allowExpired=false) {
    const u=new URL(imageUrl(value,allowExpired));
    if(u.hostname===privateImageHost) {
      const current=u.searchParams.get('draft_token');
      if(current && current!==draftId)throw failure('知乎图片不属于本次草稿');
      // Official editor module 81374 binds private article images to its draft ID.
      // Append only this create response's ID; preserve the signed reference bytes.
      if(!current)return u.href+'&draft_token='+encodeURIComponent(draftId);
    }
    return u.href;
  }
  function imageSignature(value,draftId) {
    const u=new URL(draftId?bindImage(value,draftId,true):imageUrl(value,true));
    if(u.hostname===privateImageHost) {
      // A read may rotate only the per-image authorization and expiration.
      // Host, exact renderer path, draft binding and all other parameters must match.
      u.searchParams.delete('auth_key');u.searchParams.delete('expiration');
      for(const key of ['animatedImageAutoPlay','incremental','precoder','sampling']) {
        const value=u.searchParams.get(key);if(/^(?:true|false)$/i.test(value||''))u.searchParams.set(key,value.toLowerCase());
      }
      u.searchParams.sort();
    }
    return u.href;
  }
  function imageShape(value,depth=0) {
    const result={type:fieldType(value)};
    if(value&&typeof value==='object'&&!Array.isArray(value)&&depth<2) {
      result.structure=shape(value);for(const key of ['url','src','original_src'])if(Object.hasOwn(value,key))result[key]=imageShape(value[key],depth+1);
    }
    if(typeof value!=='string')return result;
    result.length=value.length;
    try {
      const u=new URL(value);result.protocol=u.protocol==='https:'?'https':'other';
      if(/^[a-zA-Z0-9.-]{1,253}$/.test(u.hostname))result.host=u.hostname;
      if(u.pathname.length<=2048 && /^\/[a-zA-Z0-9_./~!$&'()*+,;=:@%\-]+$/.test(u.pathname))result.path=u.pathname;
      result.query_keys=[...new Set([...u.searchParams.keys()].map(k=>/^[a-zA-Z0-9_-]{1,64}$/.test(k)?k:'[other]'))].sort().slice(0,30);
      result.private=u.hostname===privateImageHost;
      try{imageUrl(value,true);result.valid=true;}catch{result.valid=false;}
    } catch {result.valid=false;}
    return result;
  }
  function bodyInfo(value) {
    const out={type:fieldType(value)};
    if(typeof value!=='string')return out;
    out.length=value.length;
    const doc=new DOMParser().parseFromString(value,'text/html').body;
    out.text_length=doc.textContent.length;out.top_level_tags=[...doc.children].map(n=>n.tagName.toLowerCase()).slice(0,80);
    out.image_count=doc.querySelectorAll('img').length;out.link_count=doc.querySelectorAll('a[href]').length;out.code_count=doc.querySelectorAll('pre').length;
    out.blocks=[...doc.children].slice(0,80).map(n=>({tag:n.tagName.toLowerCase(),text_length:n.textContent.length,children:[...n.children].map(x=>x.tagName.toLowerCase()).slice(0,20)}));
    out.images=[...doc.querySelectorAll('img')].slice(0,30).map(n=>({src:imageShape(n.getAttribute('src')),original_src:imageShape(n.getAttribute('data-original-src')),caption_length:(n.getAttribute('data-caption')||'').length}));
    return out;
  }
  function bodySignature(value,draftId) {
    const root=new DOMParser().parseFromString(value,'text/html').body;
    // The live editor inserts empty top-level paragraphs and serializes each
    // unordered-list item as a separate ul. Normalize only these observed forms;
    // nested structure, numbered-list restarts and substantive separators remain.
    for(const node of [...root.children]) {
      if(node.tagName==='P' && node.attributes.length===0 && node.children.length<=1 &&
          [...node.children].every(n=>n.tagName==='BR'&&n.attributes.length===0) &&
          [...node.childNodes].every(n=>n.nodeType===1 || n.nodeType===3&&!/[^ \t\r\n\f]/.test(n.nodeValue)))node.remove();
    }
    for(const list of [...root.children]) {
      if(list.tagName!=='UL'||list.attributes.length)continue;
      let next=list.nextElementSibling;
      while(next?.tagName==='UL' && !next.attributes.length) {
        let between=list.nextSibling,plain=true;
        while(between&&between!==next){if(between.nodeType!==3||/[^ \t\r\n\f]/.test(between.nodeValue))plain=false;between=between.nextSibling;}
        if(!plain)break;
        list.append(...next.childNodes);next.remove();next=list.nextElementSibling;
      }
    }
    const headingLevels=[...new Set([...root.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(n=>n.tagName))].sort();
    const merge=parts=>{const result=[];for(const part of parts.flat()){if(typeof part==='string'&&typeof result[result.length-1]==='string')result[result.length-1]+=part;else result.push(part);}return result;};
    const walk=node=>{
      if(node.nodeType===3)return node.nodeValue.replace(/[ \t\r\n\f]+/g,' ');
      if(node.nodeType===8)return [];
      if(node.nodeType!==1)throw failure('正文含未支持的节点，保留原草稿');
      let tag=node.tagName.toLowerCase();
      if(tag==='pre') {
        const code=node.querySelector('code'),classLanguage=code?.className.match(/(?:^|\s)language-([^\s]+)/)?.[1]||'';
        const language=node.getAttribute('lang')||classLanguage;
        if(node.getAttribute('lang')&&classLanguage&&node.getAttribute('lang')!==classLanguage)throw failure('代码语言字段不一致');
        return {tag:'pre',text:node.textContent.replace(/\r\n/g,'\n'),language:language==='text'?'':language};
      }
      if(node.getAttribute('style')?.trim()&&tag!=='img')throw failure('正文行内样式转换尚未核实');
      if(tag==='img')return {tag:'img',source:imageSignature(node.getAttribute('src'),draftId),caption:node.getAttribute('data-caption')||''};
      if(tag==='br'||tag==='hr')return {tag};
      if(tag==='figure') {
        const images=[...node.children].filter(n=>n.tagName==='IMG'),captions=[...node.children].filter(n=>n.tagName==='FIGCAPTION');
        if(images.length!==1||captions.length>1||[...node.children].some(n=>!['IMG','FIGCAPTION'].includes(n.tagName))||[...node.childNodes].some(n=>n.nodeType===3&&n.nodeValue.trim()))throw failure('图片图块结构尚未核实');
        const picture=walk(images[0]),caption=captions[0]?.textContent||'';
        if(picture.caption&&caption&&picture.caption!==caption)throw failure('图片说明字段不一致');
        picture.caption=caption||picture.caption;return picture;
      }
      if(!['p','div','span','ul','ol','li','blockquote','code','strong','b','em','i','u','s','del','a','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','td','th'].includes(tag))throw failure('正文含未支持的元素，保留原草稿');
      let children=merge([...node.childNodes].map(walk));
      if(['p','div'].includes(tag)&&children.some(n=>n?.tag==='img')&&children.every(n=>typeof n==='string'?!n.trim():n.tag==='img'))return children.filter(n=>typeof n!=='string');
      if(tag==='span')return children;
      if(tag==='div')throw failure('正文div转换尚未核实');
      const block=n=>n&&typeof n==='object'&&/^(?:p|ul|ol|li|blockquote|pre|table|thead|tbody|tr|td|th|heading-[0-9]+)$/.test(n.tag);
      if(['ul','ol','li','blockquote','table','thead','tbody','tr'].includes(tag))children=children.filter((n,i,all)=>typeof n!=='string'||n.trim()||(!block(all[i-1])&&!block(all[i+1])));
      if(/^h[1-6]$/.test(tag))tag='heading-'+(headingLevels.indexOf(node.tagName)+1);
      tag=({b:'strong',i:'em',s:'del'})[tag]||tag;
      const result={tag,children};
      if(tag==='a')result.href=node.getAttribute('href')||'';
      if(tag==='ol'){result.start=node.getAttribute('start')||'1';result.reversed=node.hasAttribute('reversed');}
      if(['ul','ol','li'].includes(tag))for(const key of ['type','value','dir'])if(node.hasAttribute(key))result[key]=node.getAttribute(key);
      if(tag==='code')result.language=(node.className.match(/(?:^|\s)language-([^\s]+)/)||[])[1]||'';
      if(['td','th'].includes(tag)){result.colspan=node.getAttribute('colspan')||'1';result.rowspan=node.getAttribute('rowspan')||'1';}
      return result;
    };
    return merge([...root.childNodes].map(walk)).filter(n=>typeof n!=='string'||n.trim());
  }
  function imageEquivalence(draftId,observed) {
    const cache=new Map();let count=0,deadline=0;
    const digest=async(url,proof,label)=>{
      if(!deadline)deadline=Date.now()+15000;
      const remaining=()=>{const ms=deadline-Date.now();if(ms<=0)throw failure('图片只读核对超时');return ms;};
      const original=new URL(url),expiration=original.searchParams.get('expiration');
      if(original.hostname===privateImageHost && expiration!==null && Number(expiration)>=0 && Date.now()/1000>=Number(expiration)) {
        // Official modules 5543/40326 refresh the same expired private URL by GET.
        // Article editor images carry only this original draft's binding.
        const bound=bindImage(url,draftId,true),endpoint=new URL('https://api.zhihu.com/image/private_url');
        endpoint.searchParams.set('scene','article_draft');endpoint.searchParams.set('url',bound);
        const refresh=await fetch(endpoint.href,{method:'GET',credentials:'include',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(remaining())});
        proof[label+'_refresh_http_status']=refresh.status;if(!refresh.ok)throw failure('原图片引用只读刷新未完成');
        const data=await refresh.json(),updated=data?.urls_list?.[bound];proof[label+'_refresh_type']=fieldType(updated);
        const originalKey=new URL(imageSignature(bound,draftId)),updatedKey=new URL(imageSignature(updated,draftId));
        originalKey.searchParams.delete('source');updatedKey.searchParams.delete('source');
        proof[label+'_refresh_same_image']=originalKey.href===updatedKey.href;
        if(!proof[label+'_refresh_same_image'])throw failure('原图片引用刷新返回不同图片');
        url=imageUrl(updated);
      }
      const response=await fetch(url,{method:'GET',credentials:'include',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(remaining())});
      proof[label+'_http_status']=response.status;
      if(!response.ok || !/^image\//i.test(response.headers.get('content-type')||''))throw failure('图片只读核对未取得有效图像');
      const limit=5*1024*1024;if(Number(response.headers.get('content-length')||0)>limit)throw failure('图片只读核对超过大小上限');
      const reader=response.body?.getReader();if(!reader)throw failure('图片只读核对无法读取');
      let size=0;const chunks=[];
      try{while(true){const part=await reader.read();if(part.done)break;size+=part.value.length;if(size>limit||Date.now()>deadline)throw failure('图片只读核对超过限制');chunks.push(part.value);}}
      finally{await reader.cancel().catch(()=>{});}
      if(!size)throw failure('图片只读核对结果为空');proof[label+'_bytes']=size;
      const data=new Uint8Array(size);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
      return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data))).map(x=>x.toString(16).padStart(2,'0')).join('');
    };
    return async(actual,expected)=>{
      const left=imageSignature(actual,draftId),right=imageSignature(expected,draftId);
      if(left===right)return true;
      const u=new URL(left),v=new URL(right),key=left+'\n'+right;
      if(cache.has(key))return cache.get(key);
      if(u.hostname!==privateImageHost||v.hostname!==privateImageHost||u.origin!==v.origin||u.pathname!==v.pathname)return false;
      if(!/^[a-f0-9]{8}$/i.test(u.searchParams.get('source')||'')||!/^[a-f0-9]{8}$/i.test(v.searchParams.get('source')||''))return false;
      u.searchParams.delete('source');v.searchParams.delete('source');
      if(u.href!==v.href||count>=4)return false;
      count++;const expired=value=>{const e=new URL(value).searchParams.get('expiration');return e!==null&&Number(e)>=0&&Date.now()/1000>=Number(e);};
      const proof={method:'GET',credentials:'include',redirect:'error',host:u.hostname,path:u.pathname,difference:'source',actual_expired:expired(actual),expected_expired:expired(expected),actual_source:new URL(left).searchParams.get('source'),expected_source:new URL(right).searchParams.get('source'),equal:false};
      (observed.image_equivalence||(observed.image_equivalence=[])).push(proof);
      try{proof.equal=(await digest(imageUrl(actual,true),proof,'actual'))===(await digest(imageUrl(expected,true),proof,'expected'));}
      catch(e){proof.error=e?.safe?e.message:'图片只读核对未完成';}
      cache.set(key,proof.equal);return proof.equal;
    };
  }
  const imageNodes=nodes=>nodes.flatMap(node=>node&&typeof node==='object'?[...(node.tag==='img'?[node]:[]),...imageNodes(node.children||[])]:[]);
  function signatureDifferences(actual,expected) {
    const collect=(nodes,tag)=>nodes.flatMap(node=>node&&typeof node==='object'?[...(node.tag===tag?[node]:[]),...collect(node.children||[],tag)]:[]);
    const knownLanguages=new Set(['','text','plain','plaintext','auto','python','javascript','typescript','json','shell','bash','html','css','sql','c','cpp','java','go','rust','yaml','xml','markdown']);
    const language=value=>knownLanguages.has(value)?value:'[other]';
    const codesA=collect(actual,'pre'),codesB=collect(expected,'pre');
    const codes=Array.from({length:Math.max(codesA.length,codesB.length)},(_,index)=>{
      const a=codesA[index],b=codesB[index];
      const shapeCode=v=>v?{language:language(v.language),language_length:v.language.length,text_length:v.text.length,newline_count:(v.text.match(/\n/g)||[]).length,leading_newlines:(v.text.match(/^\n*/)||[''])[0].length,trailing_newlines:(v.text.match(/\n*$/)||[''])[0].length}:null;
      return {index,actual:shapeCode(a),expected:shapeCode(b),text_equal:a?.text===b?.text,language_equal:a?.language===b?.language};
    });
    const imgsA=collect(actual,'img'),imgsB=collect(expected,'img');
    const images=Array.from({length:Math.max(imgsA.length,imgsB.length)},(_,index)=>{
      const a=imgsA[index],b=imgsB[index],out={index,source_equal:a?.source===b?.source,caption_equal:a?.caption===b?.caption,actual_caption_length:a?.caption?.length??null,expected_caption_length:b?.caption?.length??null};
      if(a&&b){const u=new URL(a.source),v=new URL(b.source);out.host_equal=u.host===v.host;out.path_equal=u.pathname===v.pathname;
        out.query=[...new Set([...u.searchParams.keys(),...v.searchParams.keys()])].sort().filter(k=>/^[a-zA-Z0-9_-]{1,64}$/.test(k)).map(key=>({key,actual_present:u.searchParams.has(key),expected_present:v.searchParams.has(key),equal:u.searchParams.get(key)===v.searchParams.get(key),boolean_case_equal:/^(?:true|false)$/i.test(u.searchParams.get(key)||'')&&/^(?:true|false)$/i.test(v.searchParams.get(key)||'')&&u.searchParams.get(key).toLowerCase()===v.searchParams.get(key).toLowerCase(),actual_length:u.searchParams.get(key)?.length??null,expected_length:v.searchParams.get(key)?.length??null}));}
      return out;
    });
    return {codes,images};
  }
  const reason=(observed,error)=>'知乎'+observed.stage+'：'+(error?.safe?error.message:'响应结构未能完整核实，保留原草稿');
  async function readDraft(id,expected,observed={requests:[]}) {
    const url='https://zhuanlan.zhihu.com/p/'+id+'/edit';
    try {
      observed.stage='核对当前账号';
      const account=await identity(observed);observed.account_matches=!!account&&account.id===expected.account_id;
      if(!observed.account_matches)throw failure('当前账号与原任务不一致');
      observed.stage='读取原草稿';
      const payload=await request('/api/articles/'+id+'/draft','GET',undefined,observed);
      observed.response=shape(payload);
      const saved=payload?.data&&typeof payload.data==='object'&&!('content' in payload)&&!('title' in payload)?payload.data:payload;
      observed.draft=shape(saved);
      observed.id_type=fieldType(saved?.id);observed.slug_type=fieldType(saved?.slug);
      observed.id_matches=objectId(saved)===id && (saved?.id===undefined||saved?.slug===undefined||exactId(saved.id)===exactId(saved.slug));
      observed.title_type=fieldType(saved?.title);observed.title_matches=saved?.title===expected.title;
      observed.expected_body=bodyInfo(expected.content);observed.actual_body=bodyInfo(saved?.content);
      observed.cover_fields=Object.fromEntries(['titleImage','title_image','imageUrl','image_url','cover'].map(k=>[k,imageShape(saved?.[k])]));
      observed.expected_cover=imageShape(expected.cover);
      observed.author=shape(saved?.author);observed.author_matches=authorId(saved)?authorId(saved)===expected.account_id:null;
      observed.stage='核对草稿归属';
      if(!observed.id_matches)throw failure('原草稿编号不一致或缺失');
      if(observed.author_matches===false)throw failure('原草稿所属账号不一致');
      observed.state_type=fieldType(saved?.state);
      observed.state=typeof saved?.state==='string'&&['draft','published','deleted'].includes(saved.state)?saved.state:'unconfirmed';
      if(saved?.state!==undefined && saved.state!=='draft')throw failure('原对象尚未确认处于私有草稿状态');
      if(observed.author_matches===null || saved?.state===undefined) {
        observed.stage='核对原对象私有草稿状态';
        const owner=await request('/api/articles/'+id,'GET',undefined,observed);
        observed.owner_response=shape(owner);observed.owner_id_matches=objectId(owner)===id && (owner?.id===undefined||owner?.slug===undefined||exactId(owner.id)===exactId(owner.slug));
        observed.author_matches=authorId(owner)===expected.account_id;
        observed.owner_state_type=fieldType(owner?.state);
        observed.owner_state=typeof owner?.state==='string'&&['draft','published','deleted'].includes(owner.state)?owner.state:'unconfirmed';
        if(!observed.owner_id_matches||!observed.author_matches)throw failure('原草稿所属账号未能核实');
        if(owner?.state!=='draft')throw failure('原对象尚未确认处于私有草稿状态');
      }
      observed.stage='核对标题正文和配图';
      if(!observed.title_matches)throw failure('原草稿标题不一致');
      if(typeof saved?.content!=='string')throw failure('原草稿正文类型无效');
      const left=bodySignature(saved.content,id),right=bodySignature(expected.content,id);
      observed.signature_differences=signatureDifferences(left,right);
      const sameImage=imageEquivalence(id,observed);
      const actualImages=imageNodes(left),expectedImages=imageNodes(right);
      const actualUrls=[...new DOMParser().parseFromString(saved.content,'text/html').querySelectorAll('img')].map(n=>n.getAttribute('src'));
      const expectedUrls=[...new DOMParser().parseFromString(expected.content,'text/html').querySelectorAll('img')].map(n=>n.getAttribute('src'));
      if(actualImages.length===expectedImages.length)for(let i=0;i<actualImages.length;i++) {
        if(actualImages[i].source!==expectedImages[i].source && await sameImage(actualUrls[i],expectedUrls[i]))actualImages[i].source=expectedImages[i].source;
      }
      observed.body_matches=JSON.stringify(left)===JSON.stringify(right);
      observed.block_matches=Array.from({length:Math.max(left.length,right.length)},(_,i)=>({index:i,actual_tag:left[i]?.tag||typeof left[i],expected_tag:right[i]?.tag||typeof right[i],equal:JSON.stringify(left[i])===JSON.stringify(right[i])})).slice(0,80);
      if(!observed.body_matches)throw failure('原草稿正文结构、文字、链接或配图不一致');
      observed.stage='核对封面';
      if(expected.cover) {
        const covers=['titleImage','title_image'].filter(k=>saved[k]!==undefined&&saved[k]!==null);
        observed.cover_matches=!!covers.length;
        for(const key of covers)if(typeof saved[key]!=='string'||!(await sameImage(saved[key],expected.cover)))observed.cover_matches=false;
        if(!observed.cover_matches)throw failure('原草稿封面缺失、字段冲突或图片不一致');
      }
      observed.stage='复核当前账号';observed.final_account_matches=(await identity(observed))?.id===expected.account_id;
      if(!observed.final_account_matches)throw failure('核对期间账号发生变化');
      observed.stage='原草稿核对完成';return {id,url};
    } catch(e) {if(e?.http_status)observed.http_status=e.http_status;return {id,url,incomplete:true,reason:reason(observed,e),observed};}
  }
  try {
    if(action==='identity') {
      // Same-origin endpoint avoids depending on extension-only CORS privileges.
      return {ok:true,data:await identity()};
    }
    if(action==='upload-grant') {
      if(!/^[a-f0-9]{32}$/.test(arg?.hash||''))throw failure('图片摘要无效');
      return {ok:true,data:await request('https://api.zhihu.com/images','POST',{image_hash:arg.hash,source:'article'})};
    }
    if(action==='image-status') {
      if(!/^[a-zA-Z0-9_-]{1,200}$/.test(String(arg?.id||'')))throw failure('图片编号无效');
      return {ok:true,data:await request('https://api.zhihu.com/images/'+encodeURIComponent(arg.id))};
    }
    if(action==='image-verify') {
      const url=imageUrl(arg?.url);
      const proof=await new Promise(resolve=>{
        const img=new Image();let done=false;
        const finish=(ready,reason)=>{if(done)return;done=true;clearTimeout(timer);resolve({ready,reason,width:img.naturalWidth||0,height:img.naturalHeight||0});};
        const timer=setTimeout(()=>{finish(false,'timeout');img.src='';},12000);
        img.onload=()=>finish(img.naturalWidth>0 && img.naturalHeight>0,'loaded');
        img.onerror=()=>finish(false,'load-error');
        // Match the editor's normal same-site image request; no cookie/token is returned.
        img.referrerPolicy='strict-origin-when-cross-origin';img.src=url;
      });
      return {ok:true,data:arg?.diagnostics===true?proof:proof.ready};
    }
    if(action==='draft-read') {
      const id=exactId(arg?.draft_id);
      if(!numeric(id)||!accountId(arg?.account_id)||!arg?.title?.trim()||typeof arg?.content!=='string'||!arg.content.trim()||arg.content.includes('/media/'))throw failure('缺少原草稿编号、账号或准确提交快照，未执行核对');
      return {ok:true,data:await readDraft(id,arg)};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !arg?.content?.trim() || !accountId(arg?.account_id))throw failure('标题、正文或账号为空，尚未提交');
      const observed={stage:'提交前核对账号',requests:[]};
      const account=await identity(observed);
      if(account?.id!==arg.account_id)throw failure('知乎账号已变化，正文尚未提交');
      const doc=new DOMParser().parseFromString(arg.content,'text/html');
      for(const pre of doc.querySelectorAll('pre')) {
        const code=pre.querySelector('code'),language=code?.className.match(/(?:^|\s)language-([\w+-]+)/)?.[1];
        if(language)pre.setAttribute('lang',language);
      }
      for(const img of doc.querySelectorAll('img')) {
        imageUrl(img.getAttribute('src'));img.classList.add('content_image');img.setAttribute('data-size','normal');
      }
      for(const table of doc.querySelectorAll('table')) {
        table.setAttribute('data-draft-node','block');table.setAttribute('data-draft-type','table');table.setAttribute('data-size','normal');table.setAttribute('data-row-style','normal');
      }
      const originalCover=arg.cover?imageUrl(arg.cover):'';
      observed.stage='创建草稿';
      const created=await request('/api/articles/drafts','POST',{title:arg.title,content:'',delta_time:0},observed),id=objectId(created);
      if(!numeric(id))throw failure('未收到有效草稿编号，请先核对知乎草稿箱，不要重复发送');
      const url='https://zhuanlan.zhihu.com/p/'+id+'/edit';
      try {
        observed.stage='绑定当前草稿图片';
        for(const img of doc.querySelectorAll('img'))img.setAttribute('src',bindImage(img.getAttribute('src'),id));
        const content=doc.body.innerHTML,cover=originalCover?bindImage(originalCover,id):'';
        observed.expected_body=bodyInfo(content);observed.expected_cover=imageShape(cover);
        observed.stage='保存正文封面';
        await request('/api/articles/'+id+'/draft','PATCH',{title:arg.title,content,...(cover?{titleImage:cover,isTitleImageFullScreen:false}:{})},observed);
        return {ok:true,data:await readDraft(id,{...arg,content,cover},observed)};
      } catch(e) {if(e?.http_status)observed.http_status=e.http_status;return {ok:true,data:{id,url,incomplete:true,reason:reason(observed,e),observed}};}
    }
    if(action==='publication') {
      const draft_id=String(arg?.draft_id||''),account_id=String(arg?.account_id||'');
      if(!numeric(draft_id) || !account_id)throw failure('缺少原草稿或账号编号');
      const article=await request('/api/articles/'+draft_id);
      if(objectId(article)!==draft_id || authorId(article)!==account_id)throw failure('知乎文章编号或所属账号未能核实');
      const result=(state,message,extra={})=>({ok:true,data:{state,message,draft_id,account_id,...extra}});
      if(article.state==='draft')return result('draft','知乎仍为草稿，尚未公开发表');
      if(article.state!=='published')return result('unknown','知乎尚未确认公开发表，保留原状态');
      const url='https://zhuanlan.zhihu.com/p/'+draft_id;
      // The server must supply both an explicit published state and this exact public object.
      if(!article.url || new URL(article.url,location.origin).href!==url)throw failure('知乎公开文章链接与原草稿不一致');
      return result('published','知乎已确认公开发表，文章链接已自动同步',{article_id:draft_id,url});
    }
    return {ok:false,error:'不支持的知乎操作'};
  } catch(e){return {ok:false,error:e?.safe?e.message:'知乎响应未能核实，请查看平台诊断'};}
}
