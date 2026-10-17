async function(platform, action, arg) {
  // Weibo headline-article private drafts; this never posts a short Weibo status.
  const card='https://card.weibo.com',picture='https://picupload.weibo.com';
  if(platform!=='weibo' || (action==='upload_binary'?location.origin!==picture:![card,'https://weibo.com'].includes(location.origin)))return {ok:false,error:'请在纸飞机内完成微博登录'};
  if(!['identity','upload_binary'].includes(action) && location.origin!==card)return {ok:false,error:'请打开微博头条文章编辑器后再送草稿'};
  const numeric=x=>/^[1-9][0-9]{0,39}$/.test(String(x||'')) && (typeof x!=='number' || Number.isSafeInteger(x));
  const validPid=x=>typeof x==='string' && /^[A-Za-z0-9_-]{5,128}$/.test(x);
  async function request(url,options={}) {
    const r=await fetch(url,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000),...options});
    if(!r.ok)throw Error('微博请求失败（HTTP '+r.status+'）');
    return r;
  }
  function imageUrl(value) {
    const u=new URL(value);
    if(u.protocol!=='https:' || u.port || u.username || u.password || !u.hostname.endsWith('.sinaimg.cn') || !u.pathname.startsWith('/'))throw Error('微博未返回有效图片地址');
    return u.href;
  }
  function unquote(value) {
    // Decode only a JavaScript string literal; never execute remote page code.
    return value.replace(/\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\s\S])/g,(_,part)=>{
      if(part[0]==='u' && part.length===5 || part[0]==='x' && part.length===3)return String.fromCharCode(parseInt(part.slice(1),16));
      const escaped={'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','\\':'\\',"'":"'",'"':'"','/':'/'};
      if(!(part in escaped))throw Error('微博账号配置格式未能核实');
      return escaped[part];
    });
  }
  async function identity() {
    if(location.origin==='https://weibo.com') {
      const r=await (await request('/ajax/config/get')).json();
      if(r.data?.login===false || r.data?.login===0)return null;
      if(r.ok!==1 || !numeric(r.data?.uid))throw Error('微博登录状态未能核实');
      return {id:String(r.data.uid),name:String(r.data.screen_name || r.data.nick || r.data.uid).slice(0,200)};
    }
    const html=await (await request('/article/v5/editor')).text();
    const match=html.match(/config:\s*JSON\.parse\(\s*'((?:\\.|[^'\\])*)'\s*\)/);
    if(!match)return null;
    const config=JSON.parse(unquote(match[1]));
    if(!numeric(config.uid))throw Error('微博头条文章账号信息未能核实');
    return {id:String(config.uid),name:String(config.nick || config.uid).slice(0,200)};
  }
  async function sameAccount(id) {
    const current=await identity();
    if(!numeric(id) || !current || current.id!==String(id))throw Error('微博账号已变化，请切回原账号后核对草稿');
    return current;
  }
  function reqid(uid) {
    const seed=btoa(uid+'&'+Date.now()).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
    return (seed+crypto.randomUUID().replaceAll('-','')).slice(0,43);
  }
  async function api(path,uid,form,params={}) {
    const rid=reqid(uid),query=new URLSearchParams({uid:String(uid),...params,_rid:rid});
    const options=form===undefined?{}:{method:'POST',headers:{'content-type':'application/x-www-form-urlencoded','accept':'application/json','SN-REQID':rid},body:new URLSearchParams(form)};
    const result=await (await request('/article/v5/aj/editor/'+path+'?'+query,options)).json();
    if(!['0','100000'].includes(String(result.code)))throw Error('微博未确认操作成功，请在平台检查登录与文章权限');
    return result.data;
  }
  function uploadReceipt(result) {
    const pic=result?.data?.pics?.pic_1,pid=pic?.pid;
    if(result?.code!=='A00006' || !validPid(pid))throw Error('微博图片上传未返回有效回执');
    const url=imageUrl(pic.large?.url || pic.original?.url || pic.url || 'https://wx3.sinaimg.cn/large/'+pid+'.jpg');
    return {pid,url};
  }
  async function uploadBinary(input,uid) {
    if(!input || !numeric(uid) || !['png','jpg','jpeg','gif','webp'].includes(input.extension))throw Error('图片格式或账号无效');
    const bytes=Uint8Array.from(atob(input.base64),c=>c.charCodeAt(0));
    if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
    const mime='image/'+(['jpg','jpeg'].includes(input.extension)?'jpeg':input.extension);
    const url=picture+'/interface/pic_upload.php?app=miniblog&s=json&p=1&data=1&url=&markpos=1&logo=0&nick=&file_source=4&_rid='+reqid(uid);
    const result=await (await request(url,{method:'POST',body:new Blob([bytes],{type:mime})})).json();
    const receipt=uploadReceipt(result);
    // Return only the picture receipt; browser session data never leaves WebView2.
    return {code:'A00006',data:{pics:{pic_1:{pid:receipt.pid,large:{url:receipt.url}}}}};
  }
  async function acceptUpload(result,uid) {
    const {pid,url}=uploadReceipt(result);
    await api('plugins/uploadpic',uid,{pid,uid});
    return url;
  }
  try {
    if(action==='identity')return {ok:true,data:await identity()};
    if(action==='upload_binary')return {ok:true,data:await uploadBinary(arg,arg?.account_id)};
    if(action==='upload' || action==='upload_receipt') {
      const account=await identity();
      if(!account)throw Error('请先登录微博头条文章编辑器');
      if(action==='upload_receipt') {
        await sameAccount(arg?.account_id);
        const url=await acceptUpload(arg?.result,account.id);
        await sameAccount(account.id);
        return {ok:true,data:url};
      }
      const uploaded=await uploadBinary(arg,account.id);
      await sameAccount(account.id);
      const url=await acceptUpload(uploaded,account.id);
      await sameAccount(account.id);
      return {ok:true,data:url};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !arg?.content?.trim() || [arg.content,arg.markdown||'',arg.cover||''].some(value=>value.includes('/media/')))throw Error('标题、正文或图片未准备好');
      const cover=arg.cover?imageUrl(arg.cover):'';
      const account=await sameAccount(arg.account_id);
      let content=arg.content;
      content=content.replace(/<img\b[^>]*>/gi,tag=>{
        const source=tag.match(/\bsrc\s*=\s*["']([^"']+)["']/i)?.[1];
        if(!source)throw Error('微博正文图片地址无效');
        const u=new URL(imageUrl(source.replaceAll('&amp;','&')));
        const pid=u.pathname.split('/').pop().replace(/\.(?:jpg|jpeg|png|gif|webp)$/i,'');
        if(!validPid(pid))throw Error('微博正文图片尚未取得可用上传编号');
        return tag.replace(/\s+data-pid\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi,'').replace(/\s*\/?>$/,' data-pid="'+pid+'">');
      });
      const created=await api('draft/create',account.id,{});
      if(!numeric(created?.id))throw Error('未收到微博草稿编号，请先核对草稿箱，不要重复发送');
      const id=String(created.id),url=card+'/article/v5/editor#/draft/'+id;
      try {
        await sameAccount(account.id);
        await api('draft/save',account.id,{id,title:arg.title,subtitle:'',type:'',status:'0',publish_at:'',error_msg:'',error_code:'0',collection:'[]',free_content:'',content,cover,summary:arg.summary||'',writer:'',extra:'null',is_word:'0',article_recommend:'[]',follow_to_read:'0',isreward:'0',pay_setting:'{"ispay":0,"isvclub":0}',source:'0',action:'1',content_type:'0',save:'1'},{id});
        const saved=await api('draft/load',account.id,undefined,{id});
        if(!saved || String(saved.id)!==id || String(saved.status)!=='0' || saved.title!==arg.title || saved.content!==content || (cover && saved.cover!==cover) ||
           (saved.uid!==undefined && String(saved.uid)!==account.id))throw Error('微博草稿内容或私有状态未能回读核实');
        await sameAccount(account.id);
        return {ok:true,data:{id,url}};
      } catch {return {ok:true,data:{id,url,incomplete:true}};}
    }
    return {ok:false,error:'微博此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return {ok:false,error:e.message};}
}
