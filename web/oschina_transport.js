async function(platform, action, arg) {
  if(platform!=='oschina' || !['https://my.oschina.net','https://www.oschina.net'].includes(location.origin))return {ok:false,error:'请在纸飞机内完成开源中国登录'};
  const api='https://apiv1.oschina.net/oschinapi';
  const numeric=v=>/^[1-9][0-9]{0,15}$/.test(String(v||'')) && Number.isSafeInteger(Number(v));
  async function request(path, options={}) {
    const r=await fetch(api+path,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000),...options});
    if(!r.ok)throw Error('开源中国请求失败（HTTP '+r.status+'）');
    const value=await r.json();
    if(value.code!==200 || value.success===false) {
      const error=Error('开源中国未完成请求（代码 '+String(value.code||'未知')+'）');
      error.signedOut=[40001,40002,40003,40005].includes(value.code);
      throw error;
    }
    return value.result;
  }
  async function identity() {
    try {
      const u=await request('/user/myDetails');
      if(!numeric(u?.userId))throw Error('开源中国账号编号无效');
      return {id:String(u.userId),name:String(u.userVo?.name||u.userId)};
    } catch(e) {if(e.signedOut)return null;throw e;}
  }
  function imageUrl(value) {
    const u=new URL(value);
    if(u.protocol!=='https:' || u.port || u.username || u.password || !u.hostname.endsWith('.oschina.net'))throw Error('开源中国图片地址未能核实');
    return u.href;
  }
  function draftContent(article) {
    let content=article.markdown;
    if(article.cover) {const cover=imageUrl(article.cover);if(!content.includes(cover))content='![封面]('+cover+')\n\n'+content;}
    return content;
  }
  function ownerIds(value) {
    const ids=[];
    for(const key of ['space','user']) {
      const raw=value?.[key];
      if(raw===undefined || raw===null || raw==='')continue;
      const id=typeof raw==='object'?raw.id:raw;
      if(!numeric(id))throw Error('原草稿所属账号字段未能识别');
      ids.push(String(id));
    }
    return ids;
  }
  async function listedOwner(id, account, observed) {
    // Official blog-drafts: GET /api/draft/list takes pageNum/pageSize only,
    // then item.space/item.id build its editor URL (no arbitrary user filter).
    // https://static.oschina.net/oschina/space/v2/js/index-legacy-DQ6qdc26.js
    // pageResult-legacy-CX1Vo1pe.js supports array, records and list.
    if((await identity())?.id!==account.id)throw Error('核对草稿列表前账号已变化');
    const pageSize=10, seen=new Set();
    observed.owner_source='authenticated-draft-list';observed.list_pages=0;observed.list_matches=false;
    for(let page=1;page<=10;page++) {
      const value=await request('/api/draft/list?pageNum='+page+'&pageSize='+pageSize);
      const rows=Array.isArray(value)?value:Array.isArray(value?.records)?value.records:Array.isArray(value?.list)?value.list:null;
      observed.list_pages=page;observed.list_shape=Array.isArray(value)?'array':Array.isArray(value?.records)?'records':Array.isArray(value?.list)?'list':'unknown';
      if(!rows)throw Error('当前账号草稿列表结构未能核实');
      const matches=rows.filter(row=>numeric(row?.id) && String(row.id)===id);
      if(matches.length>1)throw Error('当前账号草稿列表返回重复编号，未认定归属');
      if(matches.length===1) {
        const owners=ownerIds(matches[0]);
        observed.list_owner_present=owners.length>0;
        if(owners.some(owner=>owner!==account.id))throw Error('草稿列表所属账号与原任务不同');
        if((await identity())?.id!==account.id)throw Error('核对草稿列表期间账号已变化');
        observed.list_matches=true;
        return true;
      }
      const signature=rows.map(row=>numeric(row?.id)?String(row.id):'?').join(',');
      if(!rows.length || seen.has(signature))break;
      seen.add(signature);
      const pages=Number(value?.pages),size=Number(value?.size)||pageSize;
      if(Number.isSafeInteger(pages) && pages>0 ? page>=pages : rows.length<size)break;
    }
    throw Error('当前账号草稿列表尚未找到原编号，保留待核对状态');
  }
  async function readDraft(id, article, account) {
    const url='https://my.oschina.net/u/'+account.id+'/blog/ai-write/draft/'+id;
    let observed={};
    try {
      const saved=await request('/api/draft/edit/detail?id='+id);
      // The current OSC editor stores this identifier as draft, while older responses use id.
      const savedId=String(saved?.draft||saved?.id||'');
      const owners=ownerIds(saved);
      const type=value=>value===null?'null':Array.isArray(value)?'array':typeof value;
      observed={structure:Object.keys(saved||{}).filter(k=>!/token|secret|cookie|csrf|session|password|authorization/i.test(k)).slice(0,60).map(k=>k+':'+type(saved[k])),
        id_matches:savedId===id,owner_matches:owners.length>0 && owners.every(owner=>owner===account.id),owner_source:owners.length?'detail':'missing',title_matches:saved?.title===article.title,
        content_matches:saved?.content===draftContent(article),content_type:type(saved?.content),content_type_value:[1,2,'1','2'].includes(saved?.contentType)?saved.contentType:null,
        content_length:typeof saved?.content==='string'?saved.content.length:null,expected_length:draftContent(article).length,
        space_type:type(saved?.space),user_type:type(saved?.user),draft_type:type(saved?.draft)};
      if(savedId!==id)throw Error('原草稿编号未能核实');
      if(owners.length && owners.some(owner=>owner!==account.id))throw Error('原草稿所属账号未能核实');
      if(!owners.length)observed.owner_matches=await listedOwner(id,account,observed);
      if((await identity())?.id!==account.id)throw Error('核对期间原草稿账号已变化');
      if(saved.title!==article.title)throw Error('原草稿标题回读不一致');
      if(saved.content!==draftContent(article))throw Error('原草稿正文或图片回读不一致');
      if(![1,'1'].includes(saved.contentType))throw Error('原草稿正文格式类型回读不一致');
      return {id,url};
    } catch(e) {return {id,url,incomplete:true,reason:e.message,observed};}
  }
  try {
    if(action==='identity')return {ok:true,data:await identity()};
    if(action==='upload') {
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw Error('图片格式无效');
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
      const form=new FormData();
      form.append('file',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);
      return {ok:true,data:imageUrl(await request('/ai/creation/project/uploadDetail',{method:'POST',body:form}))};
    }
    if(action==='draft-read') {
      const id=String(arg?.draft_id||'');
      if(!numeric(id) || !arg?.title?.trim() || !arg?.markdown?.trim())throw Error('缺少原草稿编号或任务正文，未执行核对');
      const account=await identity();
      if(!account || account.id!==arg.account_id)throw Error('开源中国账号已变化，请重新核对');
      return {ok:true,data:await readDraft(id,arg,account)};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !arg?.markdown?.trim() || arg.markdown.includes('/media/'))throw Error('标题、正文或图片未准备好');
      const account=await identity();
      if(!account || account.id!==arg.account_id)throw Error('开源中国账号已变化，请重新核对');
      const content=draftContent(arg);
      // This endpoint saves a draft. Never call blog/web/add or blog/web/edit.
      const created=await request('/api/draft/save_draft',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({title:arg.title,user:Number(account.id),content,contentType:1,
          catalog:0,originUrl:'',privacy:false,disableComment:false})});
      if(!numeric(created?.id))throw Error('未收到有效草稿编号，请核对草稿箱，不要重复发送');
      return {ok:true,data:await readDraft(String(created.id),arg,account)};
    }
    return {ok:false,error:'开源中国此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return {ok:false,error:e.message};}
}
