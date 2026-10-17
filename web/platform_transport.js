async function(platform, action, arg) {
  // Platform protocol contracts checked against upstream adapters on 2026-10-07.
  // Cookies stay in WebView2; ImageX temporary grants are consumed in native memory only.
  const origins = {csdn:'https://editor.csdn.net', juejin:'https://juejin.cn'};
  if (location.origin !== origins[platform]) return {ok:false,error:'请先完成平台登录'};
  const utf = s => new TextEncoder().encode(s);
  const hex = b => Array.from(new Uint8Array(b), x=>x.toString(16).padStart(2,'0')).join('');
  const hash = async s => hex(await crypto.subtle.digest('SHA-256',utf(s)));
  const hmac = async (key,s) => crypto.subtle.sign('HMAC',await crypto.subtle.importKey('raw',
    typeof key === 'string' ? utf(key) : key,{name:'HMAC',hash:'SHA-256'},false,['sign']),utf(s));
  const base64 = b => btoa(String.fromCharCode(...new Uint8Array(b)));
  async function request(url, options={}) {
    let response;
    try { response = await fetch(url,{...options,signal:AbortSignal.timeout(20000)}); }
    catch { const target=new URL(url);throw Error('无法连接 '+target.hostname+target.pathname+'，请求未完成'); }
    if (!response.ok) throw Error('平台请求失败（HTTP '+response.status+'）');
    return response;
  }
  async function csdn(path, data) {
    const method = data === undefined ? 'GET':'POST', nonce = crypto.randomUUID();
    const headers = {accept:'*/*','x-ca-key':'203803574','x-ca-nonce':nonce,
      'x-ca-signature-headers':'x-ca-key,x-ca-nonce'};
    const parsed=new URL(path,'https://bizapi.csdn.net');
    const query=[...parsed.searchParams].sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>encodeURIComponent(k)+(v?'='+encodeURIComponent(v):'')).join('&');
    const canonicalPath=parsed.pathname+(query?'?'+query:'');
    const sign = `${method}\n*/*\n\n${method==='POST'?'application/json':''}\n\nx-ca-key:203803574\nx-ca-nonce:${nonce}\n${canonicalPath}`;
    // Public web-client signing constants, not a user's credential.
    headers['x-ca-signature'] = base64(await hmac('9znpamsyl2c7cdrr9sas0le9vbc3r6ba',sign));
    if(data!==undefined) headers['content-type']='application/json';
    const r=await (await request('https://bizapi.csdn.net'+path,{method,headers,credentials:'include',
      ...(data===undefined?{}:{body:JSON.stringify(data)})})).json();
    if(r.code!==200) throw Error('CSDN未接受请求，请检查登录或平台提示');
    return r.data;
  }
  async function juejin(path, data, extra={}) {
    const r=await (await request('https://api.juejin.cn'+path,{credentials:'include',
      ...(data===undefined?{}:{method:'POST',body:JSON.stringify(data)}),
      headers:{'content-type':'application/json',...extra}})).json();
    if(r.err_no) throw Error('掘金未接受请求，请检查登录或平台提示');
    return r.data;
  }
  async function imagex(action, token, session='') {
    const url=new URL('https://imagex.bytedanceapi.com/');
    url.searchParams.set('Action',action);url.searchParams.set('Version','2018-08-01');
    url.searchParams.set('ServiceId','73owjymdk6');
    if(session)url.searchParams.set('SessionKey',session);
    url.searchParams.sort();
    const method=session?'POST':'GET', stamp=new Date().toISOString().replace(/[:-]|\.\d{3}/g,''), day=stamp.slice(0,8);
    const headers={'x-amz-date':stamp,'x-amz-security-token':token.SessionToken};
    const names='x-amz-date;x-amz-security-token';
    const query=[...url.searchParams].map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
    const canonical=[method,'/',query,
      `x-amz-date:${stamp}\nx-amz-security-token:${token.SessionToken}\n`,names,await hash('')].join('\n');
    const scope=day+'/cn-north-1/imagex/aws4_request';
    let key=await hmac('AWS4'+token.SecretAccessKey,day);
    for(const part of ['cn-north-1','imagex','aws4_request'])key=await hmac(key,part);
    headers.Authorization=`AWS4-HMAC-SHA256 Credential=${token.AccessKeyId}/${scope}, SignedHeaders=${names}, Signature=${hex(await hmac(key,'AWS4-HMAC-SHA256\n'+stamp+'\n'+scope+'\n'+await hash(canonical)))}`;
    const r=await (await request(url.href,{method,headers})).json();
    if(!r.Result || r.ResponseMetadata?.Error)throw Error('图片上传服务未确认请求');
    return r.Result;
  }
  function httpsHost(value, suffixes) {
    const url=new URL(value);
    if(url.protocol!=='https:' || url.username || url.password || url.port ||
       !suffixes.some(s=>url.hostname===s || url.hostname.endsWith('.'+s)))throw Error('图片服务地址未通过检查');
    return url.href;
  }
  try {
    if(platform==='juejin' && action==='upload-token') {
      const value=await juejin('/imagex/v2/gen_token?aid=2608&client=web&uuid='+crypto.randomUUID());
      if(!value?.token?.AccessKeyId || !value.token.SecretAccessKey || !value.token.SessionToken)throw Error('未取得有效图片上传凭证');
      return {ok:true,data:value.token};
    }
    if(platform==='juejin' && action==='image-url') {
      const value=await juejin('/imagex/v2/get_img_url?aid=2608&client=web&uuid='+crypto.randomUUID()+'&uri='+encodeURIComponent(arg.uri)+'&img_type=private');
      return {ok:true,data:httpsHost(value.main_url||value.backup_url,['byteimg.com','juejin.cn'])};
    }
    if(action==='identity') {
      const u=platform==='csdn' ? await csdn('/blog-console-api/v3/editor/getBaseInfo') : await juejin('/user_api/v1/user/get');
      const id=platform==='csdn'?u?.name:u?.user_id;
      return {ok:true,data:id?{id:String(id),name:String(platform==='csdn'?(u.nickname||id):(u.user_name||id))}:null};
    }
    if(action==='publication') {
      const draft_id=String(arg?.draft_id||''), account_id=String(arg?.account_id||'');
      if(!/^\d+$/.test(draft_id) || !account_id)throw Error('缺少原草稿编号或登录账号，无法核对');
      const result=(state,message,extra={})=>({ok:true,data:{state,message,draft_id,account_id,...extra}});
      if(platform==='juejin') {
        const payload=await juejin('/content_api/v1/article_draft/detail',{draft_id});
        const draft=payload?.article_draft;
        if((payload?.id && String(payload.id)!==draft_id) ||
           (payload?.article_id && String(payload.article_id)!==String(draft?.article_id||'')))throw Error('草稿关联数据不一致，保留原状态');
        if(String(draft?.id||'')!==draft_id)throw Error('平台返回的草稿编号不一致，未更新状态');
        const draftAuthor=String(payload.author_user_info?.user_id || draft.user_id || '');
        if(draftAuthor!==account_id || (draft.user_id && String(draft.user_id)!==account_id))throw Error('草稿所属账号与当前账号不一致或未能核实');
        const article_id=String(draft.article_id||'');
        if(!article_id || article_id==='0')return result('draft','已送草稿；在平台确认发表后自动同步结果');
        if(!/^\d+$/.test(article_id))throw Error('平台返回的文章编号无效');
        const article=await juejin('/content_api/v1/article/detail',{
          article_id,forbid_count:true,is_pre_load:true,req_from:0,client_type:2608});
        const info=article?.article_info;
        if(String(article?.article_id||'')!==article_id || (info?.article_id && String(info.article_id)!==article_id) || String(info?.draft_id||'')!==draft_id)
          throw Error('无法确认公开文章对应原草稿，保留原状态');
        if(String(article?.author_user_info?.user_id||'')!==account_id ||
           (info.user_id && String(info.user_id)!==account_id))throw Error('文章所属账号与送稿账号不一致');
        if(info.audit_status===2)return result('published','掘金已确认公开发表，文章链接已自动同步',
          {article_id,url:'https://juejin.cn/post/'+article_id});
        if([0,1,3].includes(info.audit_status))return result('reviewing','掘金审核中，审核通过后自动同步发表结果',{article_id});
        return result('unknown',info.audit_status===-1?'掘金审核未通过，请打开平台查看原因':'掘金尚未确认公开发表，保留原状态',{article_id});
      }
      if(platform==='csdn') {
        const base=await csdn('/blog-console-api/v3/editor/getBaseInfo');
        if(String(base?.name||'')!==account_id)throw Error('当前平台账号与送稿账号不同');
        if(base.is_muser)return result('unknown','当前为代管账号，请使用原作者账号核对发表结果');
        const article=await csdn('/blog-console-api/v3/editor/getArticle?id='+encodeURIComponent(draft_id)+'&model_type=');
        if(String(article?.article_id||'')!==draft_id)throw Error('平台返回的文章编号与原稿不一致');
        const status=String(article.status);
        const listStatus=status==='2'?'draft':['7','16','6','9','99'].includes(status)?'audit':status==='1'?'all_v2':'all_v3';
        // The editor response has no documented author field. Membership in the
        // authenticated author's own article list supplies the ownership link,
        // including before binding an old draft with no stored sender account.
        let matched=null;
        for(let page=1;page<=5;page++) {
          const listing=await csdn('/blog/phoenix/console/v1/article/list?page='+page+'&pageSize=20&status='+listStatus);
          if(!Array.isArray(listing?.list))throw Error('平台文章列表响应无效');
          matched=listing.list.find(item=>String(item.articleId)===draft_id);
          if(matched || listing.list.length<20 || Number(listing.total)<=page*20)break;
        }
        if(!matched)return result('unknown','尚未在当前账号的文章列表确认本篇，保留原状态');
        if(status==='2' && String(matched.status)==='2')return result('draft','已送草稿；在平台确认发表后自动同步结果');
        if(['7','16'].includes(status) && ['7','16'].includes(String(matched.status)))return result('reviewing','CSDN审核中，审核通过后自动同步发表结果',{article_id:draft_id});
        if(status!=='1' || String(matched.status)!=='1' || article.read_type!=='public' || article.read_need_vip)
          return result('unknown',['6','9','99'].includes(status)?'CSDN审核未通过，请打开平台查看原因':'CSDN尚未确认公开可读，保留原状态');
        let blog;
        try{blog=new URL(base.blog_url);}catch{throw Error('平台未返回有效的作者博客地址');}
        if(blog.protocol!=='https:' || blog.hostname!=='blog.csdn.net' || blog.port || blog.username || blog.password ||
           !/^\/[^/]+\/?$/.test(blog.pathname) || blog.search || blog.hash)throw Error('作者博客地址未通过核对');
        return result('published','CSDN已确认公开发表，文章链接已自动同步',
          {article_id:draft_id,url:blog.origin+blog.pathname.replace(/\/$/,'')+'/article/details/'+draft_id});
      }
      return result('unknown','平台发表核对暂未完成，保留原状态');
    }
    if(action==='upload') {
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0)), blob=new Blob([bytes],{type:arg.mime});
      let remote;
      if(platform==='csdn') {
        const s=await csdn('/resource-api/v1/image/direct/upload/signature',{
          imageTemplate:'',appName:'direct_blog_markdown',imageSuffix:arg.extension});
        const form=new FormData();
        for(const [field,key] of Object.entries({key:'filePath',policy:'policy',signature:'signature',
          callbackBody:'callbackBody',callbackBodyType:'callbackBodyType',callbackUrl:'callbackUrl',AccessKeyId:'accessId'}))form.append(field,s[key]);
        for(const key of ['rtype','filePath','isAudit','x-image-app','type','x-image-suffix','username'])form.append('x:'+key,s.customParam[key]);
        form.append('file',blob,'image.'+arg.extension);
        const r=await (await request(httpsHost(s.host,['myhuaweicloud.com']),{method:'POST',body:form})).json();
        if(r.code!==200)throw Error('图片上传未成功');
        remote=httpsHost(r.data.imageUrl,['csdnimg.cn','csdn.net']);
      } else {
        const query='?aid=2608&client=web&uuid='+crypto.randomUUID();
        const t=(await juejin('/imagex/v2/gen_token'+query)).token;
        const s=(await imagex('ApplyImageUpload',t)).UploadAddress, store=s.StoreInfos[0];
        let crc=0xffffffff;
        for(const byte of bytes){crc^=byte;for(let n=0;n<8;n++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}
        await request(httpsHost('https://'+s.UploadHosts[0]+'/'+store.StoreUri,['byteimg.com','bytedance.com','volces.com','imagex.cn']),
          {method:'PUT',headers:{Authorization:store.Auth,'Content-Type':arg.mime,'Content-CRC32':((crc^0xffffffff)>>>0).toString(16).padStart(8,'0')},body:blob});
        const committed=await imagex('CommitImageUpload',t,s.SessionKey);
        if(!committed.Results?.some(r=>r.Uri===store.StoreUri && r.UriStatus===2000))throw Error('图片提交结果未确认');
        const r=await juejin('/imagex/v2/get_img_url'+query+'&uri='+encodeURIComponent(store.StoreUri)+'&img_type=private');
        remote=httpsHost(r.main_url||r.backup_url,['byteimg.com','juejin.cn']);
      }
      return {ok:true,data:remote};
    }
    if(action==='save') {
      let id,url;
      if(platform==='csdn') {
        const r=await csdn('/blog-console-api/v3/mdeditor/saveArticle',{
          title:arg.title,markdowncontent:arg.markdown,content:arg.content,description:arg.summary||'',
          readType:'public',level:0,tags:(arg.tags||[]).join(','),status:2,categories:'',type:'original',
          original_link:'',authorized_status:false,not_auto_saved:'1',source:'pc_mdeditor',
          cover_images:arg.cover?[arg.cover]:[],cover_type:1,is_new:1,vote_id:0,resource_id:'',pubStatus:'draft',creator_activity_id:''});
        id=String(r?.id||'');url='https://editor.csdn.net/md?articleId='+id;
      } else {
        const r=await request('https://api.juejin.cn/user_api/v1/sys/token',{method:'HEAD',credentials:'include',
          headers:{'x-secsdk-csrf-request':'1','x-secsdk-csrf-version':'1.2.10'}});
        const token=r.headers.get('x-ware-csrf-token')?.split(',')[1];
        if(!token)throw Error('无法核验掘金登录状态');
        const saved=await juejin('/content_api/v1/article_draft/create',{
          brief_content:arg.summary||'',category_id:'0',cover_image:arg.cover||'',edit_type:10,
          html_content:'deprecated',link_url:'',mark_content:arg.markdown,tag_ids:[],title:arg.title},
          {'x-secsdk-csrf-token':token});
        id=String(saved?.id||'');url='https://juejin.cn/editor/drafts/'+id;
      }
      if(!/^\d+$/.test(id))throw Error('未收到有效草稿编号');
      return {ok:true,data:{id,url}};
    }
    return {ok:false,error:'不支持的操作'};
  } catch(e) {return {ok:false,error:e.message};}
}
