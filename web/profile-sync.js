"use strict";
let profileSyncStatus=null, profileSyncConflict=null;
async function openProfileSync(){
  const s=profileSyncStatus=await api('/profile-sync/status');
  const labels={idle:'等待同步',syncing:'正在同步',synced:'已同步',conflict:'有修改需要核对',offline:'暂未同步',needs_auth:'需要配置设备连接'};
  modal('画像云端同步',`<p>本机与云端双向同步账号画像及参考图。离线可保存；联网后每分钟检查一次。</p>
    <p role="status">${esc(labels[s.state]||s.state)}${s.last_success?' · 最近成功 '+esc(fmt(s.last_success)):''}</p>
    ${s.error?`<p class="notice">${esc(s.error)}</p>`:''}
    <p class="tip">服务器：${esc(s.server||'尚未配置')}。文章、对话和发布任务暂不在这轮画像同步范围内。</p>
    ${s.conflicts.map(c=>`<p><strong>${esc(c.local.name)}</strong>：两边都修改过 <button class="button" data-action="profile-sync-conflict" data-id="${esc(c.id)}">核对两份</button></p>`).join('')}
    <details><summary>连接设置</summary><label>服务器地址<input id="sync-server" value="${esc(s.server)}" placeholder="https://…"></label>
    <label>设备 ID<input id="sync-client-id" autocomplete="off" placeholder="已配置时可留空"></label>
    <label>设备密钥<input id="sync-client-secret" type="password" autocomplete="new-password" placeholder="已配置时可留空"></label>
    <p class="tip">凭据仅保存在本机加密配置中，不进入画像或文章。清空服务器地址可停用同步。</p>
    <button class="button" data-action="profile-sync-settings">保存连接设置</button></details>`,
    '<button class="button" data-action="close">关闭</button><button class="button primary" data-action="profile-sync-run">立即同步</button>');
}
async function handleProfileSync(action,button){
  if(action==='profile-sync-open')return openProfileSync();
  if(action==='profile-sync-run'){
    await api('/profile-sync/run',{}); await refresh(false); return openProfileSync();
  }
  if(action==='profile-sync-settings'){
    const v={server:$('#sync-server').value};
    if($('#sync-client-id').value)v.client_id=$('#sync-client-id').value;
    if($('#sync-client-secret').value)v.client_secret=$('#sync-client-secret').value;
    await api('/profile-sync/settings',v);return openProfileSync();
  }
  if(action==='profile-sync-conflict'){
    profileSyncConflict=profileSyncStatus.conflicts.find(c=>c.id===button.dataset.id);
    const c=profileSyncConflict;
    modal('核对画像修改',`<p>检查两边已保存的内容，选择一份，或修改下面的合并稿再同步。</p>
      ${profileFields.map(([k,label])=>`<details><summary>${label}</summary><p><b>本机</b><br><span class="workflow-preserve">${esc(c.local[k]||'未填写')}</span></p><p><b>云端</b><br><span class="workflow-preserve">${esc(c.remote[k]||'未填写')}</span></p></details>`).join('')}
      <details><summary>参考图</summary><p>本机：${esc((c.local.visual_references||[]).join('\n')||'无')}</p><p>云端：${esc((c.remote.visual_references||[]).join('\n')||'无')}</p></details>
      <details><summary>编辑合并稿</summary>${profileFields.map(([k,label,limit])=>`<label>${label}<textarea id="sync-merge-${k}" maxlength="${limit}">${esc(c.local[k]||'')}</textarea></label>`).join('')}
      <p class="tip">合并稿沿用本机参考图；参考图不同可先选择完整版本，再到画像页调整。</p><button class="button" data-action="profile-sync-resolve" data-choice="merge">同步合并稿</button></details>`,
      '<button class="button" data-action="profile-sync-open">返回</button><button class="button" data-action="profile-sync-resolve" data-choice="remote">采用云端版</button><button class="button primary" data-action="profile-sync-resolve" data-choice="local">采用本机版</button>');return;
  }
  if(action==='profile-sync-resolve'){
    const c=profileSyncConflict, v={id:c.id,local_hash:c.local_hash,remote_hash:c.remote_hash,choice:button.dataset.choice};
    if(v.choice==='merge')v.profile={...Object.fromEntries(profileFields.map(([k])=>[k,$('#sync-merge-'+k).value])),visual_references:c.local.visual_references||[]};
    await api('/profile-sync/resolve',v);await api('/profile-sync/run',{});await refresh(false);return openProfileSync();
  }
}
