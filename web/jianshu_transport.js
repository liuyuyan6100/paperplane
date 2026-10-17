async function(platform, action, arg) {
  // Protocol: Jianshu's current writer app (2026-10-08), not the publicize API.
  const diagnostic={origin:location.origin,path:location.pathname||'',stage:'origin',source:'',status:0,fields:[]};
  if(platform!=='jianshu' || location.origin!=='https://www.jianshu.com')return {ok:false,error:'请在纸飞机内完成简书登录',diagnostic};
  const numeric=x=>/^[1-9][0-9]{0,39}$/.test(String(x||'')) && (typeof x!=='number' || Number.isSafeInteger(x));
  async function request(url, options={}) {
    const r=await fetch(url,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000),...options});
    if(!r.ok){const error=Error('简书请求失败（HTTP '+r.status+'）');error.httpStatus=r.status;throw error;}
    return r;
  }
  async function json(url,options={}) {return (await request(url,options)).json();}
  function headers() {
    const csrf=document.querySelector('meta[name="csrf-token"]')?.content;
    return {'content-type':'application/json','accept':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})};
  }
  function imageUrl(value) {
    const u=new URL(value);
    if(u.protocol!=='https:' || u.port || u.username || u.password ||
       !/^upload-images\.jianshu\.(?:io|com)$/.test(u.hostname) || !u.pathname.startsWith('/upload_images/'))throw Error('简书未返回有效图片地址');
    return u.href;
  }
  async function identity() {
    let primaryError;
    diagnostic.stage='identity';diagnostic.source='/author/current_user';diagnostic.status=0;diagnostic.fields=[];
    try {
      // The current editor reads the user's stable numeric ID directly. Default
      // avatars contain no user ID and must never be our only identity source.
      const user=await json('/author/current_user');
      diagnostic.status=200;
      diagnostic.fields=['id','nickname','slug','avatar'].filter(key=>user && Object.hasOwn(user,key));
      if(numeric(user?.id) && typeof user.nickname==='string')return {id:String(user.id),name:user.nickname.slice(0,200)};
      primaryError='编辑器账号接口未返回稳定账号编号';
    } catch(e) {
      diagnostic.status=e.httpStatus||0;
      if(e.httpStatus===401)return null;
      primaryError=e.httpStatus?'编辑器账号接口HTTP '+e.httpStatus:'编辑器账号接口连接或解析失败';
    }
    const primaryStatus=diagnostic.status;
    diagnostic.source='/settings/basic.json';diagnostic.status=0;diagnostic.fields=[];
    try {
      const result=await json('/settings/basic.json'),user=result.data;
      diagnostic.status=200;
      diagnostic.fields=['id','nickname','avatar'].filter(key=>user && Object.hasOwn(user,key));
      if(!user)throw Error('基本资料接口未返回账号');
      const avatarId=String(user.avatar||'').match(/^https:\/\/[^/]+\.jianshu\.(?:io|com)\/users\/upload_avatars\/([1-9][0-9]*)\//)?.[1];
      const id=user.id || avatarId;
      if(!numeric(id) || typeof user.nickname!=='string')throw Error('基本资料接口未返回稳定账号编号（默认头像不含编号）');
      return {id:String(id),name:user.nickname.slice(0,200)};
    } catch(e) {
      diagnostic.primary_status=primaryStatus;
      diagnostic.status=e.httpStatus||diagnostic.status;
      const detail=e.httpStatus?'基本资料接口HTTP '+e.httpStatus:diagnostic.status===200?'基本资料接口未返回稳定账号编号':'基本资料接口连接或解析失败';
      throw Error('简书账号未能核实：'+primaryError+'；'+detail+'。请在内置编辑器检查登录状态');
    }
  }
  async function sameAccount(id) {
    const current=await identity();
    if(!numeric(id) || !current || current.id!==String(id))throw Error('简书账号已变化，请切回原账号后核对草稿');
  }
  async function metadata(book,id) {
    const rows=await json('/author/notebooks/'+book+'/notes?page=1&count=100&nid='+id);
    if(!Array.isArray(rows))throw Error('简书未返回有效草稿列表');
    const note=rows.find(row=>String(row.id)===id);
    if(!note || note.shared!==false || note.schedule_publish_at)throw Error('简书草稿编号或私有状态未能核实');
    return note;
  }
  try {
    if(action==='identity')return {ok:true,data:await identity(),diagnostic};
    if(action==='upload') {
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw Error('图片格式无效');
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
      const account=await identity();
      if(!account)throw Error('请先登录简书');
      const filename='paperplane-'+crypto.randomUUID()+'.'+arg.extension;
      const grant=await json('/upload_images/token.json?filename='+encodeURIComponent(filename));
      if(typeof grant.token!=='string' || !grant.token || typeof grant.key!=='string' || !grant.key)throw Error('简书未返回有效图片上传授权');
      const form=new FormData();
      form.append('token',grant.token);form.append('key',grant.key);form.append('x:protocol','https');
      form.append('file',new Blob([bytes],{type:arg.mime}),filename);
      const uploaded=await json('https://upload.qiniup.com',{method:'POST',credentials:'omit',body:form});
      const url=imageUrl(uploaded.url);
      await sameAccount(account.id);
      return {ok:true,data:url};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !arg?.markdown?.trim() || !arg?.content?.trim() ||
         [arg.markdown,arg.content,arg.cover||''].some(value=>value.includes('/media/')))throw Error('标题、正文或图片未准备好');
      const cover=arg.cover?imageUrl(arg.cover):'';
      await sameAccount(arg.account_id);
      const books=await json('/author/notebooks');
      if(!Array.isArray(books) || !numeric(books[0]?.id))throw Error('请先在简书创建文集，然后再送稿');
      const book=String(books[0].id);
      const created=await json('/author/notes',{method:'POST',headers:headers(),body:JSON.stringify({notebook_id:book,title:arg.title,at_bottom:false})});
      if(!numeric(created.id))throw Error('未收到简书草稿编号，请先核对草稿箱，不要重复发送');
      const id=String(created.id),url='https://www.jianshu.com/writer#/notebooks/'+book+'/notes/'+id;
      // Retain the exact draft even when the later write or read-back is uncertain.
      try {
        const before=await metadata(book,id);
        if(![1,2].includes(before.note_type) || !Number.isSafeInteger(before.autosave_control) || before.autosave_control<0)throw Error('简书编辑格式或保存版本未能核实');
        let content=before.note_type===2?arg.markdown:arg.content;
        if(cover && !content.includes(cover))content=before.note_type===2?'![]('+cover+')\n\n'+content:'<p><img src="'+cover.replaceAll('&','&amp;').replaceAll('"','&quot;')+'"></p>'+content;
        await sameAccount(arg.account_id);
        await json('/author/notes/'+id,{method:'PUT',headers:headers(),body:JSON.stringify({id,autosave_control:before.autosave_control+1,title:arg.title,content})});
        const saved=await json('/author/notes/'+id+'/content');
        const after=await metadata(book,id);
        if(after.title!==arg.title || after.note_type!==before.note_type || saved.content!==content)throw Error('简书草稿内容未能回读核实');
        await sameAccount(arg.account_id);
        return {ok:true,data:{id,url}};
      } catch {return {ok:true,data:{id,url,incomplete:true}};}
    }
    return {ok:false,error:'简书此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return {ok:false,error:e.message,...(action==='identity'?{diagnostic}:{})};}
}
