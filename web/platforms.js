"use strict";
const channelCheckRun = {queue:[],busy:false,current:'',message:''};
function channelCheckLabel(p) {
  const r=p.connection_check;
  return r ? `${esc(r.note)}<small>最近检查：${esc(r.at)}；这是当次结果，配置变化后请重验。</small>` : '尚未执行连接检查';
}
function platformNames() { return Object.fromEntries((state.channels || []).map(p => [p.id, p.name])); }
function repurposeTargets() { return (state.channels || []).filter(p => p.enabled && p.repurpose); }
function platformApplyLabel(id) {
  const p = state.channels.find(p => p.id === id);
  return !p?.delivery_implemented ? '采用平台稿' : p.send_operation === 'draft' ? '采用并送草稿' : '采用并发表';
}
function platformDetails(p) {
  const images = {manual:'随发布包导出', upload:'已有上传适配', 'public-url':'仅公开图片网址', local:'本机图片'};
  return `<small>${esc(p.capability_label)} · ${(p.formats || []).map(esc).join(' / ')}<br>配图：${esc(images[p.images] || '待接入')} · 草稿：${p.draft ? (p.verification?.draft?.status === 'verified' ? '已验通' : '已有适配，待验通') : '待接入'} · 公开发表：${p.publish ? (p.verification?.publish?.status === 'verified' ? '已验通' : '已有适配，待验通') : '待接入'}<br>账号：${esc(p.account || '待接入')}</small>`;
}
function directoryButton() { return '<button class="button small" data-action="platform-directory">＋ 添加平台</button>'; }
function drawPlatforms() {
  const rows = state.channels || [];
  $('#main').innerHTML = heading('渠道管理','启用、登录和送稿验证分别管理；停用不删除账号、文章或发布记录。',`<button class="button primary" data-action="channel-verify-all" ${channelCheckRun.busy?'disabled':''}>验证全部未验通渠道</button><button class="button" data-action="channel-refresh">刷新状态</button>`) +
    `<div role="status" class="notice">${esc(channelCheckRun.message || '验证先检查连接和账号；配置缺失时进入设置，登录失效时打开平台窗口。真实送稿验通仍需测试草稿回执，不自动公开发表。')}${channelCheckRun.queue.length&&!channelCheckRun.busy?'<button class="button small" data-action="channel-verify-resume">处理后继续验证</button><button class="button small" data-action="channel-verify-skip">跳过当前渠道</button>':''}</div>` +
    `<p class="notice">已启用 ${rows.filter(p=>p.enabled).length} / 已收录 ${rows.length} 个渠道（含手动发布包）。自动送稿已接入 ${rows.filter(p=>p.delivery_implemented && p.kind!=='local').length} 个。登录成功不会自动启用渠道。</p>` +
    `<div class="channel-management">${rows.map(p=>`<section class="panel panel-body" data-managed-channel="${esc(p.id)}">
      <div class="panel-head">${channelLogo(p.id)}<h2>${esc(p.name)}</h2><span class="tag ${p.enabled?'green':''}">${p.enabled?'已启用':'未启用'}</span></div>
      <dl><dt>账号登录</dt><dd>${esc(p.account || '尚未接入账号核验')}</dd>
      <dt>送稿能力</dt><dd>${esc(p.kind==='local'?'本地保存':p.delivery_implemented?'自动送稿适配已接入':'手动发布包；自动送稿未接入')}</dd>
      <dt>实际验证</dt><dd>${p.kind==='local'?'无需远端送稿':`草稿：${p.verification?.draft?.status==='verified'?'已验通':'未验通'} · 公开发表：${p.publish ? (p.verification?.publish?.status==='verified'?'已验通':'未验通') : '由你在平台发表'}`}</dd>
      <dt>连接检查</dt><dd>${channelCheckLabel(p)}</dd>
      <dt>平台限制</dt><dd>${channelRestriction(p)}</dd></dl>
      <div class="actions"><button class="button" data-action="channel-toggle" data-platform="${esc(p.id)}" data-enabled="${!p.enabled}">${p.enabled?'停用渠道':'启用渠道'}</button>
      <button class="button" data-action="channel-verify" data-platform="${esc(p.id)}" ${channelCheckRun.busy?'disabled':''}>验证并处理</button>
      ${p.desktop_login?`<button class="button" data-action="publishing-open" data-platform="${esc(p.id)}" ${p.login_busy?'disabled':''}>${p.login_state==='verified'?'重新核验 / 打开后台':'登录 / 核验账号'}</button>`:p.config?.editor_url?`<a class="button" href="${safeLink(p.config.editor_url)}" target="_blank" rel="noopener noreferrer">打开平台后台</a>`:''}
      ${['wechat','devto','xiaohongshu'].includes(p.id)?'<a class="button" href="#settings">配置连接</a>':''}</div>
    </section>`).join('')}</div>`;
}
function channelRestriction(p) {
  if(p.login_only)return '当前仅接入登录核验；自动上传、保存草稿和发表核对尚未接通。';
  if(p.id==='weibo')return '自动保存微博头条文章草稿，普通短微博不在此发送；公开发表由你在平台完成。';
  if(p.id==='devto')return '文章使用API Key；本地图片通过内置DEV窗口上传，两个入口须为同一账号。';
  if(p.id==='cnblogs')return '需先开通博客；申请及审核结果请在平台后台查看，登录不代表已获送稿权限。';
  if(['oschina','segmentfault','51cto','toutiao','bilibili-article','jianshu'].includes(p.id))return '只送草稿；公开发表由你在平台完成，发表结果暂需人工登记。';
  if(['csdn','juejin','zhihu','xiaohongshu'].includes(p.id))return '只送草稿；公开发表由你在平台完成。登录过期需重新核验。';
  if(p.kind==='local')return '本地保存，无需平台登录。';
  if(p.id==='wechat')return '需要公众号接口权限；送入草稿箱不等于公开发表。';
  return p.delivery_implemented?'以平台接口权限和发布检查结果为准。':'可使用手动发布包，尚未接入自动送稿。';
}
async function openPlatformDirectory() {
  const rows = (await api('/platforms')).platforms;
  const groups = {existing:'先完成已有渠道',next:'优先新增',later:'随后扩展',optional:'按需启用'};
  modal('添加平台 / 平台目录', '<p class="tip">启用后出现在对话、改编和发布入口。内容格式是改编目标；已有适配不等于发送验通。手动补记发表不会升级发送能力。</p>' + Object.entries(groups).map(([key,name]) => `<section class="panel panel-body spaced"><h3>${esc(name)}</h3>${rows.filter(p=>p.group===key).map(p=>`<div class="setting-channel"><label class="check-label"><input type="checkbox" data-platform-choice="${esc(p.id)}" ${p.enabled?'checked':''}>${esc(p.name)}</label><div class="channel-info">${platformDetails(p)}</div></div>`).join('')}</section>`).join(''), '<button class="button" data-action="close">取消</button><button class="button primary" data-action="platform-save">保存平台选择</button>');
}
async function handlePlatformAction(button) {
  if(button.dataset.action==='draft-recheck') {
    button.disabled=true;
    try {
      const draft_id=$('#draft-recheck-id')?.value.trim() || '';
      await api('/jobs/'+button.dataset.id+'/draft-recheck',{draft_id});
      await refresh();
      $('#modal').close();
      toast('原草稿内容已核实，未重复送稿');
    } finally {button.disabled=false;}
    return true;
  }
  if(button.dataset.action==='channel-verify-all') {
    if(channelCheckRun.busy)return true;
    channelCheckRun.queue=(state.channels||[]).filter(p=>p.kind!=='local' && (p.verification?.[p.send_operation]?.status!=='verified' || p.login_state!=='verified')).map(p=>p.id);
    await runChannelChecks();return true;
  }
  if(button.dataset.action==='channel-verify') {
    if(channelCheckRun.busy)return true;
    channelCheckRun.queue=[button.dataset.platform];await runChannelChecks();return true;
  }
  if(['channel-verify-resume','channel-verify-skip'].includes(button.dataset.action)) {
    if(channelCheckRun.busy)return true;
    if(button.dataset.action==='channel-verify-skip')channelCheckRun.queue.shift();
    await runChannelChecks();return true;
  }
  if(button.dataset.action==='channel-refresh') { await refresh(); return true; }
  if(button.dataset.action==='channel-toggle') {
    button.disabled=true;
    try {
      await api('/platforms',{channels:[{id:button.dataset.platform,enabled:button.dataset.enabled==='true'}]});
      await refresh();toast('渠道启用状态已保存');
    } finally {button.disabled=false;}
    return true;
  }
  if(button.dataset.action==='platform-directory') { await openPlatformDirectory(); return true; }
  if(button.dataset.action==='platform-save') {
    const channels = $$('[data-platform-choice]').map(el=>({id:el.dataset.platformChoice,enabled:el.checked}));
    await api('/platforms',{channels});
    state = await api('/state');
    $('#modal').close();
    toast('平台选择已保存，各入口使用同一目录');
    if(view==='repurpose') await drawRepurpose();
    if(view==='home') drawHome();
    if(view==='platforms') drawPlatforms();
    return true;
  }
  return false;
}

async function runChannelChecks() {
  if(channelCheckRun.busy)return;
  channelCheckRun.busy=true;
  try {
    while(channelCheckRun.queue.length) {
      const id=channelCheckRun.queue[0],name=platformNames()[id]||id;
      channelCheckRun.current=id;channelCheckRun.message=`正在验证 ${name}，剩余 ${channelCheckRun.queue.length} 个。`;
      if(view==='platforms')drawPlatforms();
      const r=await api('/platforms/verify',{channel:id});
      await refresh(false);
      if(r.action || r.status==='busy') {
        channelCheckRun.message=`${name}：${r.note} 处理后返回渠道管理继续验证，也可跳过此项。`;
        if(r.action==='settings'){location.hash='#settings';}
        if(r.action==='login')await api('/publishing-bridge/open',{platform:id});
        if(r.action==='xhs-login')await api('/xhs-drafts/open',{});
        return;
      }
      channelCheckRun.queue.shift();
    }
    channelCheckRun.message='本轮连接检查完成。各渠道结果已记录；未开发接口和未测试的草稿、发表能力仍保留原状态。';
  } catch(e) {
    channelCheckRun.message='验证暂停：'+e.message+'。可重试或跳过当前渠道。';
  } finally {
    channelCheckRun.busy=false;
    if(view==='platforms')drawPlatforms();
  }
}
