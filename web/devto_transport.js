async function(platform, action, arg) {
  // Forem d98655f8f894df738594789519c2bc3a1b9ea733:
  // AsyncInfoController#base_data and article-form/actions.js#generateMainImage.
  // Only images use the browser session. Articles stay on the configured API route.
  if(platform!=='devto' || location.origin!=='https://dev.to')return {ok:false,error:'请在纸飞机内完成DEV登录'};
  const diagnostic={stage:'session'};
  const numeric=value=>/^[1-9][0-9]{0,15}$/.test(String(value||'')) && Number.isSafeInteger(Number(value));
  async function session() {
    let response;
    try {response=await fetch('/async_info/base_data',{credentials:'same-origin',redirect:'error',cache:'no-store',
      headers:{accept:'application/json'},signal:AbortSignal.timeout(25000)});}
    catch {throw Error('DEV账号核验请求未完成，请检查登录或平台服务状态');}
    if(!response.ok)throw Error('DEV账号核验失败（HTTP '+response.status+'）');
    const value=await response.json();
    if(!value || typeof value!=='object' || typeof value.token!=='string' || !value.token)throw Error('DEV账号核验响应无效');
    if(!value.user)return {account:null};
    let user;
    try {user=typeof value.user==='string'?JSON.parse(value.user):value.user;}
    catch {throw Error('DEV账号资料无效');}
    if(!numeric(user?.id) || typeof user.name!=='string' || !user.name.trim())throw Error('DEV账号编号或名称未能核实');
    return {account:{id:String(user.id),name:user.name},csrf:value.token};
  }
  function imageUrl(value) {
    if(typeof value!=='string')throw Error('DEV未返回有效图片地址');
    let url;try {url=new URL(value);}catch {throw Error('DEV图片地址无效');}
    if(url.protocol!=='https:' || url.port || url.username || url.password || url.hash)throw Error('DEV图片地址未能核实');
    const host=url.hostname,path=url.pathname;
    diagnostic.image={host,path_prefix:path.split('/').slice(0,3).join('/'),query_keys:[...url.searchParams.keys()].filter(k=>/^[a-zA-Z0-9_-]{1,50}$/.test(k)).slice(0,12)};
    const uploadPath=path.startsWith('/uploads/articles/');
    const allowed=(host==='dev.to' && (uploadPath || path.startsWith('/remoteimages/uploads/articles/'))) ||
      ((['dev-to-uploads.s3.amazonaws.com','thepracticaldev.s3.amazonaws.com'].includes(host) || /^(?:dev-to-uploads|thepracticaldev)\.s3\.[a-z]{2}-[a-z]+-[1-9]\.amazonaws\.com$/.test(host)) && uploadPath) ||
      ['media.dev.to','media2.dev.to'].includes(host) || (host==='res.cloudinary.com' && path.startsWith('/practicaldev/'));
    if(!allowed)throw Error('DEV图片托管地址未能核实');
    return url.href;
  }
  try {
    if(!['identity','upload','diagnose'].includes(action))throw Error('DEV内置窗口仅用于账号核验和图片上传');
    const auth=await session();
    if(action==='identity')return {ok:true,data:auth.account};
    if(action==='diagnose')return {ok:true,data:{authenticated:!!auth.account,last_upload:globalThis.__paperplaneDevImageDiagnostic||null}};
    if(!auth.account)throw Error('请先在纸飞机内登录DEV');
    if(!numeric(arg?.account_id) || auth.account.id!==String(arg.account_id))throw Error('DEV浏览器账号与API送稿账号不同，图片未上传');
    const mimes={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp'};
    if(!arg || !Object.hasOwn(mimes,arg.extension) || arg.mime!==mimes[arg.extension] ||
       typeof arg.base64!=='string' || arg.base64.length>6700000)throw Error('DEV图片格式或大小无效');
    const bytes=Uint8Array.from(atob(arg.base64),character=>character.charCodeAt(0));
    if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
    const form=new FormData();
    form.append('authenticity_token',auth.csrf);
    form.append('image[]',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);
    let response;
    try {response=await fetch('/image_uploads',{method:'POST',credentials:'same-origin',redirect:'error',
      headers:{'X-CSRF-Token':auth.csrf,accept:'application/json'},body:form,signal:AbortSignal.timeout(60000)});}
    catch {throw Error('DEV图片上传结果未确认，正文尚未发送');}
    if(!response.ok)throw Error('DEV图片上传失败（HTTP '+response.status+'），正文尚未发送');
    const result=await response.json();
    if(result?.error || !Array.isArray(result?.links) || result.links.length!==1)throw Error('DEV图片上传未返回唯一有效回执');
    diagnostic.stage='image-receipt';
    let url;try {url=imageUrl(result.links[0]);}finally {globalThis.__paperplaneDevImageDiagnostic={...diagnostic};}
    if((await session()).account?.id!==auth.account.id)throw Error('DEV上传期间账号已变化，正文尚未发送');
    return {ok:true,data:url};
  } catch(error) {return {ok:false,error:error.message,diagnostic};}
}
