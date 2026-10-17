"use strict";
let profileLearningView = null, profileLearningSequence = 0, profileLearningPoll = null;
const learningStatus = {queued:'等待整理',running:'正在整理',completed:'已整理',failed:'整理失败',interrupted:'学习已中断',paused:'已暂停'};
function profileLearningTabs(id, active='basic') {
  return `<nav class="actions profile-learning-tabs" aria-label="画像内容">${[['basic','基本设定'],['experience','创作经验'],['pending','待核对'],['updates','更新记录']].map(([tab,label])=>`<a class="button ${tab===active?'primary':''}" ${tab===active?'aria-current="page"':''} href="#profile/${esc(id)}${tab==='basic'?'':'/'+tab}">${label}</a>`).join('')}</nav>`;
}
function learningSource(m) {
  const s=m.source||{};
  return `<details><summary>来源与适用范围</summary><p>平台：${esc(memoryPlatforms()[m.platform]||m.platform)} · 主题：${esc(m.keywords||'所有主题')}</p><p>${esc(s.title||'作者反馈')} · ${esc(fmt(s.at))}${s.version?' · 来源v'+esc(s.version):''}</p><blockquote class="workflow-preserve">${esc(s.quote||'')}</blockquote>${s.reason?`<p>${esc(s.reason)}</p>`:''}${s.source_changed?'<p class="tip">整理期间画像已有变化，本条沿用旧来源，确认前请核对。</p>':''}${s.post_id?`<a href="#edit/${esc(s.post_id)}">查看来源文章</a>`:''}<details><summary>保存时的来源片段</summary><pre class="chat-preview">${esc(s.text||'')}</pre></details></details>`;
}
function learningMemoryCard(m, owner) {
  const conflicts=m.source?.conflicts||[];
  return `<article class="panel panel-body spaced" data-learning-memory="${esc(m.id)}"><div class="actions"><span class="tag">${esc(memoryStates[m.status])}</span><span>${esc(m.source?.category||'创作经验')} · v${m.version}</span></div><p class="workflow-preserve"><strong>${esc(m.content)}</strong></p>${conflicts.length?`<p class="notice amber">与已有要求有冲突，记住前需要核对：${esc(conflicts.map(id=>id==='legacy'?'画像原有反馈':id==='profile'?'基本设定':owner.memories.find(x=>x.id===id)?.content||'旧经验').join('；'))}</p>`:''}${learningSource(m)}<div class="actions spaced">${m.status==='pending'?'<button class="button primary" data-action="profile-learning-confirm">确认记住</button><button class="button" data-action="profile-learning-ignore">忽略</button><button class="button" data-action="profile-learning-article_only">仅保留本篇依据</button>':''}${m.status==='accepted'?'<button class="button" data-action="profile-learning-undo">撤销本次更新</button><button class="button" data-action="profile-learning-disable">停用</button>':''}${['disabled','deleted'].includes(m.status)?'<button class="button" data-action="profile-learning-restore">恢复为待核对</button>':''}${m.status!=='deleted'?'<button class="button" data-action="profile-learning-edit">修改</button>':''}</div></article>`;
}
function learningEventCard(e) {
  return `<article class="panel panel-body spaced"><div class="actions"><strong>${esc(e.title)}</strong><span class="tag">${esc(learningStatus[e.status]||e.status)}</span></div><p class="tip">${esc(fmt(e.updated_at))}${e.parts_done?' · 已处理 '+e.parts_done+' 段':''}</p>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(e.model_provenance):''}${e.result.note?`<p>${esc(e.result.note)}</p>`:''}${(e.result.observations||[]).map(x=>`<p>${esc(x.content)}：${esc(x.reason)}</p>`).join('')}${e.error?`<p class="error-inline">${esc(e.error)}</p>`:''}<div class="actions">${['failed','interrupted','paused'].includes(e.status)?`<button class="button" data-action="profile-learning-event" data-id="${esc(e.id)}" data-operation="retry">${e.status==='paused'?'继续整理':'按原来源重试'}</button>`:''}${e.status==='queued'?`<button class="button" data-action="profile-learning-event" data-id="${esc(e.id)}" data-operation="pause">暂停此项</button>`:''}</div></article>`;
}
async function drawProfileLearning(id, tab) {
  clearTimeout(profileLearningPoll);
  const seq=++profileLearningSequence, hash=location.hash;
  if(!profileLearningView||profileLearningView.profile.id!==id)$('#main').innerHTML=heading('画像学习','正在读取当前画像…');
  const d=await api(`/profiles/${id}/learning`);
  if(seq!==profileLearningSequence||location.hash!==hash)return;
  profileLearningView=d;
  const header=heading(esc(d.profile.name),'画像随写作和作者反馈积累。基本设定与本机经验一起用于后续创作。',`<a class="button" href="#profiles">返回画像列表</a><button class="button" data-action="profile-learning-refresh">刷新</button>`)+profileLearningTabs(id,tab);
  let content='';
  if(tab==='experience') {
    content=`<section class="panel panel-body spaced"><h2>当前画像的积累</h2><p>已生效 ${d.memories.filter(m=>m.status==='accepted').length} 条 · <a href="#profile/${id}/pending">待核对 ${d.pending_count} 条</a></p><p class="tip">本机经验独立保存，暂不参与画像云端同步。已开始任务沿用原快照。</p><div class="actions"><button class="button" data-action="profile-learning-options">${d.settings.enabled?'暂停从创作中学习':'恢复从创作中学习'}</button><button class="button" data-action="profile-learning-organize">整理已有创作</button><button class="button" data-action="profile-learning-manual">添加或提炼经验</button></div></section><section class="panel panel-body spaced"><h3>画像原有反馈</h3><p class="workflow-preserve">${esc(d.profile.memory||'尚未填写。')}</p><p class="tip">原有文字仍参与创作，不计作新增经验条目。</p></section>`+d.memories.filter(m=>m.status==='accepted').map(m=>learningMemoryCard(m,d)).join('');
    if(!d.memories.some(m=>m.status==='accepted'))content+='<p class="empty">暂无新增的已生效经验。画像的基本设定和原有反馈仍会用于创作。</p>';
    const inactive=d.memories.filter(m=>['disabled','deleted'].includes(m.status));
    if(inactive.length)content+=`<details class="spaced"><summary>已停用或忽略 · ${inactive.length} 条</summary>${inactive.map(m=>learningMemoryCard(m,d)).join('')}</details>`;
  } else if(tab==='pending') {
    content='<p class="tip spaced">从修改中推测的偏好，需要你核对后才生效。每条保留来源和适用范围，可以调整、忽略或仅保留本篇依据。</p><button class="button primary" data-action="profile-learning-confirm-many">记住勾选的经验</button>'+d.memories.filter(m=>m.status==='pending').map(m=>`${!m.source?.conflicts?.length?`<label class="check-label spaced"><input type="checkbox" data-learning-batch="${esc(m.id)}">将下方这条经验加入本次确认</label>`:''}${learningMemoryCard(m,d)}`).join('');
    if(!d.pending_count)content+='<p class="empty">暂无待核对经验。</p>';
  } else {
    const actions={propose:'提出候选',confirm:'确认记住',remember_direct:'按明确长期要求记住',edit:'修改后重新待核对',disable:'停用',undo:'撤销本次更新',ignore:'忽略',article_only:'仅保留本篇依据',restore:'恢复为待核对',delete:'移除',replaced:'已由新经验替换'};
    content='<h2 class="spaced">反馈整理进度</h2>'+d.events.map(learningEventCard).join('')+(d.events.length?'':'<p class="empty">暂无学习记录。文章修改和反馈的整理结果会显示在这里。</p>');
    content+='<h2 class="spaced">画像与经验更新</h2>'+[...d.history.map(r=>({at:r.at,text:`${actions[r.action]||r.action} · ${r.snapshot.content} · v${r.version}`})),...d.profile_history.map(r=>({at:r.at,text:`基本设定 · ${profileRevisionActions[r.action]||r.action} · v${r.version}`}))].sort((a,b)=>b.at.localeCompare(a.at)).map(r=>`<p class="panel panel-body"><span>${esc(r.text)}</span><br><small>${esc(fmt(r.at))}</small></p>`).join('');
  }
  $('#main').innerHTML=header+content;
  if(d.events.some(e=>['queued','running'].includes(e.status)))profileLearningPoll=setTimeout(()=>{
    if(location.hash===hash&&!$('#modal').open&&!document.querySelector('[data-learning-batch]:checked'))drawProfileLearning(id,tab).catch(e=>toast(e.message));
  },3000);
}
async function handleProfileLearningAction(action, button) {
  if(action==='profile-learning-post-exclude' && editor?.profile_id) {
    await api(`/profiles/${editor.profile_id}/learning/exclude`,{post_id:editor.id,excluded:button.dataset.excluded!=='true'});
    return loadArticleLearning(editor.id);
  }
  const d=profileLearningView;if(!d)return;
  const base=`/profiles/${d.profile.id}`,card=button.closest('[data-learning-memory]');
  const m=card&&d.memories.find(x=>x.id===card.dataset.learningMemory);
  if(action==='profile-learning-refresh')return drawProfileLearning(d.profile.id,location.hash.split('/')[2]);
  if(action==='profile-learning-options')await api(base+'/learning/settings',{enabled:!d.settings.enabled,version:d.settings.version});
  else if(action==='profile-learning-confirm-many') {
    const ids=[...document.querySelectorAll('[data-learning-batch]:checked')].map(x=>x.dataset.learningBatch);
    await api(base+'/learning/confirm-many',{items:ids.map(id=>({id,version:d.memories.find(m=>m.id===id).version}))});
  }
  else if(action==='profile-learning-event')await api(base+'/learning/events/'+button.dataset.id,{action:button.dataset.operation});
  else if(action==='profile-learning-manual')return openMemories(d.profile.id);
  else if(action==='profile-learning-open-basic'){closeModal();location.hash='profile/'+d.profile.id;return;}
  else if(action==='profile-learning-edit'&&m){await openMemories(d.profile.id);return memoryForm(m);}
  else if(action==='profile-learning-organize') {
    modal('整理已有创作',`<p>仅处理你选中的本画像文章和原有反馈，可能调用模型并消耗额度；结果先供核对，不改正文。默认选择母稿。</p><label class="check-label"><input type="checkbox" id="learning-legacy" ${d.profile.memory?'checked':''}>整理画像原有反馈</label><div class="learning-post-list">${d.posts.map(p=>`<label class="check-label"><input type="checkbox" data-learning-post="${esc(p.id)}" ${!p.source_post_id&&!p.excluded?'checked':''} ${p.excluded?'disabled':''}>${esc(p.title)} · v${p.version}${p.source_post_id?' · 平台稿':''}${p.excluded?' · 已排除':''}</label>`).join('')||'<p>此画像暂无可整理的文章。</p>'}</div>`, '<button class="button" data-action="close">返回</button><button class="button primary" data-action="profile-learning-organize-start">开始整理选中内容</button>');return;
  } else if(action==='profile-learning-organize-start') {
    const ids=[...document.querySelectorAll('[data-learning-post]:checked')].map(x=>x.dataset.learningPost);
    await api(base+'/learning/organize',{post_ids:ids,include_legacy:$('#learning-legacy').checked});closeModal();toast('已按选定范围排队整理');location.hash=`profile/${d.profile.id}/updates`;return;
  } else if(action==='profile-learning-confirm'&&m) {
    const conflicts=m.source?.conflicts||[];
    if(conflicts.length) {
      const olds=conflicts.map(id=>d.memories.find(x=>x.id===id)).filter(x=>x?.status==='accepted');
      profileLearningView.confirming=m;
      modal('核对经验冲突',`<p>新经验：${esc(m.content)}</p>${olds.map(o=>`<label class="check-label"><input type="checkbox" data-learning-replace="${esc(o.id)}" data-version="${o.version}">停用旧经验：${esc(o.content)} · v${o.version}</label>`).join('')}${conflicts.some(x=>['legacy','profile'].includes(x))?`<p>此项与基本设定或原有反馈冲突，请先在<button class="button small" data-action="profile-learning-open-basic">基本设定</button>中调整冲突内容。</p><label class="check-label"><input id="learning-ack-profile" type="checkbox">我已核对当前设定，确认新经验适用</label>`:''}<p>只停用明确勾选的旧经验，保留来源和历史。</p>`, '<button class="button" data-action="close">返回</button><button class="button primary" data-action="profile-learning-resolve">确认上述处理并记住</button>');return;
    }
    await api(base+'/memories/'+m.id,{action:'confirm',version:m.version});
  } else if(action==='profile-learning-resolve') {
    const target=d.confirming;const replacements=Object.fromEntries([...document.querySelectorAll('[data-learning-replace]:checked')].map(x=>[x.dataset.learningReplace,Number(x.dataset.version)]));
    await api(base+'/learning/resolve/'+target.id,{version:target.version,replacements,profile_version:d.profile.version,ack_profile:$('#learning-ack-profile')?.checked||false});closeModal();
  } else if(m)await api(base+'/memories/'+m.id,{action:action.replace('profile-learning-',''),version:m.version});
  if(location.hash.startsWith('#profile/'+d.profile.id+'/'))return drawProfileLearning(d.profile.id,location.hash.split('/')[2]);
}
async function loadArticleLearning(postId) {
  const element=$('#article-learning');if(!element)return;
  try {
    const d=await api(`/posts/${postId}/learning`);
    if($('#article-learning')!==element||editor?.id!==postId)return;
    if(!d.profile_id){element.innerHTML='<p class="tip">选择账号画像后，可从本篇修改中整理创作经验。</p>';return;}
    const latest=d.events[0];
    element.innerHTML=`<div class="actions"><strong>画像学习</strong><span>${d.excluded?'本篇不参与学习':!d.enabled?'自动学习已暂停':latest?esc(learningStatus[latest.status]||latest.status):'保存修改后，后台整理可复用反馈'}</span><a href="#profile/${esc(d.profile_id)}/updates">查看进度和经验</a><button class="button small" data-action="profile-learning-post-exclude" data-excluded="${d.excluded}">${d.excluded?'允许从此篇学习':'不从此篇学习'}</button></div>${latest?.error?`<p class="error-inline">${esc(latest.error)}；正文已保存。</p>`:''}`;
    if(latest&&['queued','running'].includes(latest.status))setTimeout(()=>{if(editor?.id===postId&&$('#article-learning')===element)loadArticleLearning(postId);},3000);
  } catch(error){if($('#article-learning')===element)element.innerHTML=`<p class="tip">学习进度暂不可读取：${esc(error.message)}。正文保存不受影响。</p>`;}
}
function chatLearningHtml(events) {
  return (events||[]).map(e=>`<section class="notice blue"><strong>画像学习 · ${esc(learningStatus[e.status]||e.status)}</strong>${e.error?`<p>${esc(e.error)}</p>`:''}${(e.memories||[]).map(m=>`<p>${m.status==='accepted'?'已记住':m.status==='pending'?'待核对':'已停用或忽略'}：${esc(m.content)}</p><a class="button small" href="#profile/${esc(m.profile_id)}/${m.status==='pending'?'pending':'experience'}">${m.status==='accepted'?'查看 / 撤销':'核对经验'}</a>`).join('')}${e.status==='completed'&&!e.memories?.length?`<p>${esc(e.result.note||'本次没有新的长期经验')}</p>`:''}</section>`).join('');
}
