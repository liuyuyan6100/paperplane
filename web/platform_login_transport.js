async function(platform, action) {
  const origins={toutiao:'https://mp.toutiao.com','bilibili-article':'https://member.bilibili.com',jianshu:'https://www.jianshu.com',weibo:'https://weibo.com'};
  if(action!=='identity' || location.origin!==origins[platform])return {ok:false,error:'请在对应平台窗口核验账号；尚未接入自动送稿'};
  async function request(url) {
    const r=await fetch(url,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000)});
    if(!r.ok)throw Error('账号核验请求失败（HTTP '+r.status+'），请检查平台页面');
    return r.json();
  }
  function identity(id,name) {
    if(!/^[1-9][0-9]{0,39}$/.test(String(id||'')))throw Error('平台账号信息未能核实，请在后台完成登录');
    return {ok:true,data:{id:String(id),name:String(name||id).slice(0,200)}};
  }
  try {
    if(platform==='toutiao') {
      const r=await request('/mp/agw/media/get_media_info');
      if(r.code===100004 || r.err_no===100004)return {ok:true,data:null};
      if((r.code!==undefined && r.code!==0) || (r.err_no!==undefined && r.err_no!==0))throw Error('头条号未返回有效账号，请在后台检查开通状态');
      return identity(r.data?.user?.id,r.data?.user?.screen_name);
    }
    if(platform==='bilibili-article') {
      const r=await request('https://api.bilibili.com/x/web-interface/nav');
      if(r.code===-101)return {ok:true,data:null};
      if(r.code!==0 || r.data?.isLogin!==true)throw Error('B站登录状态未能核实');
      return identity(r.data.mid,r.data.uname);
    }
    if(platform==='jianshu') {
      const r=await request('/settings/basic.json');
      const user=r.data;
      if(!user || typeof user.nickname!=='string')throw Error('简书账号结构未能核实，请在页面检查登录状态');
      const avatarId=String(user.avatar||'').match(/^https:\/\/[^/]+\.jianshu\.(?:io|com)\/users\/upload_avatars\/([1-9][0-9]*)\//)?.[1];
      return identity(user.id || avatarId,user.nickname);
    }
    if(platform==='weibo') {
      const r=await request('/ajax/config/get');
      if(r.data?.login===false || r.data?.login===0)return {ok:true,data:null};
      if(r.ok!==1 || !r.data?.uid)throw Error('微博登录状态未能核实，请完成平台验证');
      return identity(r.data.uid,r.data.screen_name || r.data.nick);
    }
  } catch(e) {return {ok:false,error:e.message};}
}
