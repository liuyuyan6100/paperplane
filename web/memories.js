"use strict";
let memoryView = null, memorySequence = 0;
const memoryStates = {pending:'待确认',accepted:'已生效',disabled:'已停用',deleted:'已删除'};
function memoryPlatforms() { return {'':'通用', ...platformNames()}; }
function memorySnapshotHtml(profile) {
  if(!profile?.recalled_memories)return '';
  return `<details class="memory-snapshot"><summary>本次使用的画像 · ${esc(profile.name||'账号画像')}</summary><h4>基本设定</h4>${[['audience','目标读者'],['goal','运营目标'],['style','表达风格'],['preferences','偏好与红线']].filter(([k])=>profile[k]).map(([k,label])=>`<p><strong>${label}</strong>：${esc(profile[k])}</p>`).join('')}<h4>画像原有反馈</h4><p class="workflow-preserve">${esc(profile.memory||'未填写')}</p><h4>本次适用的创作经验 · ${profile.recalled_memories.length} 条</h4><p>基本设定和原有反馈仍参与创作。研究笔记与共享资料的检索结果单独显示。</p>
    ${profile.recalled_memories.length?profile.recalled_memories.map(m=>`<p><strong>${esc(m.content)}</strong><br><small>v${m.version} · ${esc(memoryPlatforms()[m.platform])} · 来源：${esc(m.source.title)}<br>原句：${esc(m.source.quote)}</small></p>`).join(''):'<p>没有新增的适用经验。</p>'}${profile.memory_exclusions?.length?`<details><summary>未用于本次创作的条目</summary>${profile.memory_exclusions.map(m=>`<p>条目 ${esc(m.id.slice(0,8))} · ${esc(m.reason)}</p>`).join('')}</details>`:''}</details>`;
}
async function openMemories(profileId) {
  const seq=++memorySequence;
  const response=await api(`/profiles/${profileId}/memories`);
  if(seq!==memorySequence)return;
  memoryView={profileId,items:response.memories};
  modal(`${esc(profileName(profileId))} · 长期记忆`, `<p>确认后的经验用于后续创作。候选、停用和删除项不会被召回；已开始任务保留原快照。</p>
    <p class="tip">此端独立保存，当前画像云端同步不包含这些经验。通用经验用于各平台；指定平台的经验只在对应平台创作时使用。</p>
    <div class="actions"><button class="button primary" data-action="profile-memory-new">＋ 添加或提炼经验</button></div>
    <div id="memory-list">${response.memories.map(m=>`<article class="panel panel-body" data-memory-id="${esc(m.id)}">
    <div class="actions"><span class="tag">${memoryStates[m.status]}</span><span>${esc(memoryPlatforms()[m.platform])} · v${m.version}</span></div>
    <p class="workflow-preserve">${esc(m.content)}</p><p class="tip">适用主题：${esc(m.keywords||'所有主题')} · ${esc(m.updated_at)}</p>
    <details><summary>来源与原句</summary><p>${esc(m.source.title)}${m.source.version?' · 来源v'+esc(m.source.version):''} · ${esc(m.source.at)}</p><blockquote>${esc(m.source.quote)}</blockquote><details><summary>保存时的来源片段</summary><pre class="chat-preview">${esc(m.source.text)}</pre></details></details>
    <div class="actions">${m.status==='pending'?'<button class="button primary" data-action="profile-memory-confirm">确认生效</button>':''}
    ${m.status==='accepted'?'<button class="button" data-action="profile-memory-disable">停用</button>':''}
    ${['disabled','deleted'].includes(m.status)?'<button class="button" data-action="profile-memory-restore">恢复为待确认</button>':''}
    ${m.status!=='deleted'?'<button class="button" data-action="profile-memory-edit">修改</button><button class="button" data-action="profile-memory-delete">删除</button>':''}
    <button class="button" data-action="profile-memory-history">修改记录</button></div></article>`).join('')||'<p class="empty">暂无经验。可以粘贴改稿反馈、复盘笔记，或从本画像文章中提炼候选。</p>'}</div>`,
    '<button class="button" data-action="close">关闭</button>');
}
function memoryForm(item) {
  const posts=(state.posts||[]).filter(p=>!p.archived && p.profile_id===memoryView.profileId);
  memoryView.editing=item||null;
  modal(item?'修改经验':'添加或提炼经验',`
    ${item?'<p>修改后重新进入待确认，原版本保留在修改记录中。</p>':`<label>来源<select id="memory-source"><option value="">粘贴反馈或复盘笔记</option>${posts.map(p=>`<option value="${esc(p.id)}" data-version="${p.version}">${esc(p.title)} · v${p.version}</option>`).join('')}</select></label>
    <label>来源说明<input id="memory-source-title" maxlength="200" value="用户反馈" placeholder="例如：07篇改稿反馈，2026-10-05"></label>
    <label>反馈或复盘原文<textarea id="memory-source-text" rows="5" maxlength="16000" placeholder="粘贴具体反馈；选择文章时使用该文章当前正文。"></textarea></label>
    <label>依据原句<textarea id="memory-quote" rows="2" maxlength="1200" placeholder="手动添加须逐字填写来源原句；AI提炼会自动选取。"></textarea></label>`}
    <label>经验内容<textarea id="memory-content" rows="4" maxlength="1200" placeholder="写清以后在什么情况下如何处理。">${esc(item?.content||'')}</textarea></label>
    <div class="form-grid"><label>平台范围<select id="memory-platform">${Object.entries(memoryPlatforms()).map(([k,v])=>`<option value="${k}" ${item?.platform===k?'selected':''}>${v}</option>`).join('')}</select></label>
    <label>适用主题<input id="memory-keywords" maxlength="120" value="${esc(item?.keywords||'')}" placeholder="逗号分隔；留空用于所有主题"></label></div>
    <p class="tip">按主题词匹配本次输入；通用偏好请留空。AI提炼结果只是候选，需要逐条核对并确认。</p><p id="memory-error" class="error-inline" role="status"></p>`,
    '<button class="button" data-action="profile-memory-back">返回</button>'+
    (item?'':'<button class="button" data-action="profile-memory-extract">AI 提炼候选</button>')+
    '<button class="button primary" data-action="profile-memory-save">保存为待确认</button>');
}
async function handleMemoryAction(action,button) {
  if(action==='profile-memory-open')return openMemories(button.dataset.id);
  if(!memoryView)return;
  if(action==='profile-memory-back')return openMemories(memoryView.profileId);
  if(action==='profile-memory-new')return memoryForm(null);
  const owner=memoryView;
  const id=button.closest('[data-memory-id]')?.dataset.memoryId;
  const item=owner.items.find(m=>m.id===id);
  if(action==='profile-memory-edit')return memoryForm(item);
  if(action==='profile-memory-history') {
    const data=await api(`/profiles/${owner.profileId}/memories/${id}/history`);
    if(memoryView!==owner)return;
    modal('经验修改记录',data.revisions.map(r=>`<article class="panel panel-body"><strong>v${r.snapshot.version} · ${memoryStates[r.snapshot.status]}</strong><p>${esc(r.snapshot.content)}</p><small>${esc(r.at)} · ${esc(memoryPlatforms()[r.snapshot.platform])} · ${esc(r.snapshot.keywords||'所有主题')}</small></article>`).join(''),'<button class="button" data-action="profile-memory-back">返回</button>');
    return;
  }
  if(owner.busy)return;
  owner.busy=true;button.disabled=true;
  try {
    const base=`/profiles/${owner.profileId}/memories`;
    if(['profile-memory-save','profile-memory-extract'].includes(action)) {
      const values={content:$('#memory-content').value,platform:$('#memory-platform').value,keywords:$('#memory-keywords').value};
      if(owner.editing)await api(base+'/'+owner.editing.id,{...values,action:'edit',version:owner.editing.version});
      else {
        const select=$('#memory-source');
        if(select.value)Object.assign(values,{source_type:'post',source_id:select.value,source_version:Number(select.selectedOptions[0].dataset.version)});
        else Object.assign(values,{source_type:'feedback',source_title:$('#memory-source-title').value,source_text:$('#memory-source-text').value});
        values.quote=$('#memory-quote').value;
        if(action==='profile-memory-extract') {
          $('#memory-error').textContent='正在提炼候选经验…';
          const result=await api(base+'/extract',values);
          if(!result.memories.length){$('#memory-error').textContent='未发现适合长期保存的经验，原有内容保持不变。';return;}
        }else await api(base,values);
      }
    }else if(item)await api(base+'/'+id,{action:action.replace('profile-memory-',''),version:item.version});
    if(memoryView===owner && button.isConnected)await openMemories(owner.profileId);
  }catch(error) {
    if($('#memory-error'))$('#memory-error').textContent=error.message;else toast(error.message);
  }finally{owner.busy=false;if(button.isConnected)button.disabled=false;}
}
