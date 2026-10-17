async function(platform, action, arg) {
  const origin=['identity','diagnose'].includes(action)?'https://home.cnblogs.com':'https://i.cnblogs.com';
  if(platform!=='cnblogs' || location.origin!==origin)return {ok:false,error:'请在纸飞机内完成博客园登录'};
  const diagnostic={stage:action==='identity'||action==='diagnose'?'identity':action};
  const numeric=x=>/^[1-9][0-9]{0,39}$/.test(String(x||''));
  async function request(url, options={}) {
    const r=await fetch(url,{credentials:'include',redirect:'error',signal:AbortSignal.timeout(30000),...options});
    if(!r.ok)throw Error('博客园请求失败（HTTP '+r.status+'）');
    return r;
  }
  function token() {
    const value=document.cookie.match(/(?:^|;\s*)XSRF-TOKEN=([^;]+)/);
    if(!value)throw Error('请先在博客园打开编辑页，完成登录后重试');
    return decodeURIComponent(value[1]);
  }
  function imageUrl(value) {
    const u=new URL(value);
    if(u.protocol!=='https:' || u.port || u.username || u.password || !u.hostname.endsWith('.cnblogs.com'))throw Error('博客园未返回有效图片地址');
    return u.href;
  }
  async function readDraft(id, expected) {
    const response=await (await request('https://i.cnblogs.com/api/articles/'+id,{cache:'no-store'})).json();
    const saved=response?.blogPost;
    const fields=Object.entries(saved||{}).filter(([key])=>/^[a-zA-Z][a-zA-Z0-9_]{0,60}$/.test(key)).map(([key,value])=>({key,
      type:value===null?'null':Array.isArray(value)?'array':typeof value,
      ...(typeof value==='boolean'||typeof value==='number'?{value}:{}),
      ...(typeof value==='string'||Array.isArray(value)?{length:value.length}:{}),
      ...(value&&typeof value==='object'&&!Array.isArray(value)?{keys:Object.keys(value).filter(k=>/^[a-zA-Z][a-zA-Z0-9_]{0,60}$/.test(k)).slice(0,40)}:{})}));
    const matches={id:String(saved?.id)===id,title:saved?.title===expected.title,body:saved?.postBody===expected.markdown,
      unpublished:saved?.isPublished===false,draft:saved?.isDraft===true,
      cover:!expected.cover||saved?.featuredImage===expected.cover};
    const result={id,url:'https://i.cnblogs.com/articles/edit;postId='+id};
    if(Object.values(matches).every(Boolean))return result;
    return {...result,incomplete:true,reason:'博客园草稿的内容或状态仍需核对',observed:{fields,matches,
      expected_body_length:expected.markdown.length,expected_cover_present:!!expected.cover}};
  }
  try {
    if(action==='identity'||action==='diagnose') {
      const r=await request('https://home.cnblogs.com/user/CurrentUserInfo',{cache:'no-store',headers:{'x-requested-with':'XMLHttpRequest',accept:'text/html, */*; q=0.01'}});
      const html=await r.text();
      if(html.length>200000)throw Error('博客园账号核验响应过大，请稍后重新核验');
      const doc=new DOMParser().parseFromString(html,'text/html');
      diagnostic.http=r.status;diagnostic.response_length=html.length;
      diagnostic.content_type=(r.headers?.get('content-type')||'').split(';')[0].slice(0,80);
      diagnostic.header_left=!!doc.querySelector('#header_user_left');diagnostic.header_right=!!doc.querySelector('#header_user_right');
      diagnostic.page_title_present=!!doc.querySelector('title');
      try {const v=JSON.parse(html);diagnostic.json_type=Array.isArray(v)?'array':typeof v;diagnostic.json_keys=v&&typeof v==='object'?Object.keys(v).filter(k=>/^[a-zA-Z0-9_]{1,32}$/.test(k)).slice(0,30):[];}catch{}
      // The welcome label is on the left; current-user profile links may be on the right.
      // Scope to both official header regions, excluding unrelated page profiles.
      const regions=[...doc.querySelectorAll('#header_user_left,#header_user_right')];
      const links=[...new Set((regions.length?regions:[doc]).flatMap(region=>[...region.querySelectorAll('a[href]')]))];
      diagnostic.header_regions=regions.map(region=>({id:region.id,tag:region.tagName.toLowerCase(),links:region.querySelectorAll('a[href]').length}));
      diagnostic.profile_link_shapes=links.slice(0,20).map(link=>{try{const u=new URL(link.getAttribute('href'),'https://home.cnblogs.com/');return {protocol:u.protocol,host:u.hostname,path:u.origin==='https://home.cnblogs.com' && /^\/[a-zA-Z0-9_./-]{0,240}$/.test(u.pathname)?u.pathname:undefined,has_query:!!u.search,has_fragment:!!u.hash};}catch{return {invalid:true};}});
      const ids=new Set();
      for(const link of links) {
        try {
          const u=new URL(link.getAttribute('href'),'https://home.cnblogs.com/');
          if(u.origin!=='https://home.cnblogs.com' || u.username || u.password || u.port || u.search || u.hash)continue;
          const match=u.pathname.match(/^\/u\/([\w.-]{1,200})\/?$/);
          if(match && !['.','..'].includes(match[1]))ids.add(match[1]);
        } catch {}
      }
      // CurrentUserInfo may omit an avatar or use an absolute profile URL.
      // Only the platform's explicit sign-in fragment proves signed-out;
      // unknown markup must preserve the remembered account as unverified.
      const right=doc.querySelector('#header_user_right');
      const signedOut=[...(right||doc).querySelectorAll('a[onclick]')].some(a=>/^\s*(?:return\s+)?login\(\s*\)\s*;?\s*$/.test(a.getAttribute('onclick')||''));
      diagnostic.profile_count=ids.size;diagnostic.link_count=links.length;diagnostic.explicit_signed_out=signedOut;
      diagnostic.logout_present=[...(right||doc).querySelectorAll('a[onclick]')].some(a=>/^\s*(?:return\s+)?logout\(\s*\)\s*;?\s*$/.test(a.getAttribute('onclick')||''));
      if(action==='diagnose')return {ok:true,data:diagnostic};
      if(ids.size===1 && !signedOut) {
        const id=[...ids][0];
        return {ok:true,data:{id,name:id}};
      }
      if(ids.size===0 && signedOut)return {ok:true,data:null};
      throw Error('博客园账号核验响应未识别（个人入口 '+ids.size+'，链接 '+links.length+'，登录入口 '+(signedOut?1:0)+'）；请重新核验');
    }
    if(action==='upload') {
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw Error('图片格式无效');
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
      const data=new FormData();
      data.append('image',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);
      data.append('app','blog');data.append('uploadType','Select');
      const r=await request('https://upload.cnblogs.com/v2/images/cors-upload',{method:'POST',headers:{'x-xsrf-token':token()},body:data});
      const value=await r.json();
      return {ok:true,data:imageUrl(value.data||value.url||value.imageUrl||value.src)};
    }
    if(action==='draft-read') {
      if(!numeric(arg?.draft_id)||!arg?.account_id||!arg?.title||!arg?.markdown)throw Error('缺少原草稿编号或提交快照，不会重新发送');
      return {ok:true,data:await readDraft(String(arg.draft_id),arg)};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !arg?.markdown?.trim() || arg.markdown.includes('/media/'))throw Error('标题、正文或图片未准备好');
      const post={id:null,postType:2,title:arg.title,postBody:arg.markdown,isMarkdown:true,
        isPublished:false,isDraft:true,accessPermission:0,usingEditorId:5,
        displayOnHomePage:false,inSiteHome:false,inSiteCandidate:false,includeInMainSyndication:false,
        isAllowComments:true,blogId:0,categoryIds:null,collectionIds:[],
        description:arg.summary||null,featuredImage:arg.cover?imageUrl(arg.cover):null};
      const created=await (await request('https://i.cnblogs.com/api/posts',{method:'POST',
        headers:{'content-type':'application/json','x-xsrf-token':token()},body:JSON.stringify(post)})).json();
      if(!numeric(created.id))throw Error('未收到草稿编号，请先核对草稿箱，不要重复发送');
      const id=String(created.id),url='https://i.cnblogs.com/articles/edit;postId='+id;
      // Once an ID exists, every read-back failure retains that exact draft.
      try {
        return {ok:true,data:await readDraft(id,arg)};
      } catch {return {ok:true,data:{id,url,incomplete:true}};}
    }
    return {ok:false,error:'博客园此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return {ok:false,error:e.message,diagnostic};}
}
