"use strict";
const repurposeLabels = {queued: "排队中", running: "生成中", review: "待审阅", applied: "已采用", failed: "失败", interrupted: "已中断"};
let repurposeCurrent = null, repurposeDirty = false, repurposeTimer = null, repurposeSequence = 0;

function repurposeRows(rows) {
  return rows.length ? rows.map(r => `<article class="workflow-history-row"><div class="row-main"><h3>${esc(r.content.title || r.source_title)}</h3><div class="row-meta"><span class="tag blue">${esc(platformNames()[r.platform])}</span><span class="tag">${esc(repurposeLabels[r.status])}</span><span>母稿 v${r.source_version} · ${esc(r.profile_name)}</span>${r.source_changed || r.profile_changed ? '<span class="tag amber">母稿、画像或长期记忆已变化</span>' : ''}</div><p>${esc(r.error || r.content.summary || r.instruction || '独立生成平台版本，保留母稿。')}</p></div><div class="actions"><button class="button small" data-action="repurpose-show" data-id="${r.id}">${r.status === 'review' ? '编辑与采用' : '查看'}</button>${r.applied_post_id ? `<a class="button small" href="#edit/${r.applied_post_id}">打开平台稿</a>` : ''}</div></article>`).join('') : empty("还没有平台改编", "从已保存的正文生成独立平台版本。");
}

async function drawRepurpose() {
  clearTimeout(repurposeTimer);
  const sequence = ++repurposeSequence;
  const source = location.hash.split('/')[1] || '';
  const candidates = state.posts.filter(p => !p.archived && !p.source_post_id && p.body.trim() && p.stage !== 'idea');
  $('#main').innerHTML = heading("平台改编", "一篇母稿，各平台独立表达。生成、采用和送稿分别记录。", directoryButton() + '<button class="button" data-action="repurpose-refresh">↻ 刷新</button>') +
    `<section class="panel panel-body"><label>选择母稿<select id="repurpose-source"><option value="">选择已保存的正文</option>${candidates.map(p => `<option value="${p.id}" ${p.id === source ? 'selected' : ''}>${esc(p.title)} · v${p.version}</option>`).join('')}</select></label><div class="actions spaced"><button class="button primary" data-action="repurpose-continue">继续未发布平台</button><button class="button" data-action="repurpose-new">另建改编</button></div><p class="tip spaced">母稿不变；每个平台稿可分别编辑与采用。母稿更新会提示版本变化，重新生成保留旧稿。默认生成 AI 配图，小红书按页生成竖版图集；接口不可用时显示具体原因。继续任务会复用已有稿件，并跳过已发布平台。</p><div id="repurpose-list" class="workflow-history">正在读取…</div></section>`;
  async function poll() {
    try {
      const rows = (await api('/repurpose' + (source ? '?post_id=' + source : ''))).variants;
      if (view !== 'repurpose' || sequence !== repurposeSequence) return;
      const published=state.posts.find(p=>p.id===source)?.publication_progress?.published||[];
      const latest=source?rows.filter((r,i)=>!published.includes(r.platform)&&rows.findIndex(x=>x.platform===r.platform)===i):rows;
      $('#repurpose-list').innerHTML = (source&&published.length?`<p class="tip">已发布，自动跳过：${published.map(id=>esc(state.channels.find(c=>c.id===id)?.name||id)).join('、')}</p>`:'')+repurposeRows(latest)+(source&&rows.length>latest.length?`<details><summary>历史改编记录</summary>${repurposeRows(rows.filter(r=>!latest.includes(r)))}</details>`:'');
      if (rows.some(r => ['queued', 'running'].includes(r.status))) repurposeTimer = setTimeout(poll, 1800);
    } catch (e) { if (view === 'repurpose' && sequence === repurposeSequence) $('#repurpose-list').textContent = e.message; }
  }
  await poll();
}

function openRepurposeCreate(post) {
  if (!post || post.source_post_id) throw new Error('请选择原母稿发起改编');
  if (!post.body.trim() || post.stage === 'idea') throw new Error('请先保存已采用的正文');
  repurposeCurrent = null;
  modal('生成平台待审稿', `<div id="repurpose-create" data-post="${post.id}" data-version="${post.version}"><h3>${esc(post.title)} · v${post.version}</h3><p class="tip">使用当前母稿和所选画像的快照。每个平台独立生成，不修改母稿，也不派发发布任务。</p><div id="repurpose-planned-model"></div><label>目标平台</label><div class="channel-choices">${repurposeTargets().map(({id, name}) => `<label class="channel-choice"><input type="checkbox" name="repurpose-platform" value="${id}" checked>${esc(name)}</label>`).join('')}</div><label>配图方式<select id="repurpose-visual-mode"><option value="ai">AI 图片（默认）</option><option value="cards">本地排版图卡</option></select></label><label>本次改编要求<textarea id="repurpose-instruction" rows="4" maxlength="6000" placeholder="例如：保留代码与亲测边界；小红书重点讲清操作步骤"></textarea></label></div>`, '<button class="button" data-action="close">取消</button><button class="button primary" data-action="repurpose-create">开始改编</button>');
  $('#repurpose-create').dataset.request = crypto.randomUUID().replaceAll('-', '');
  if(typeof renderPlannedModel==='function')void renderPlannedModel($('#repurpose-planned-model'),'repurpose');
}

function repurposeValues() {
  const c = repurposeCurrent.content;
  return {title: $('#variant-title').value, summary: $('#variant-summary').value,
    body: $('#variant-body')?.value || c.body, tags: $('#variant-tags').value.split(/[,，]/).map(t => t.trim()).filter(Boolean),
    caption: $('#variant-caption')?.value || '', pages: c.pages.map((p, i) => ({title: $(`#variant-page-title-${i}`).value, text: $(`#variant-page-text-${i}`).value, visual: $(`#variant-page-visual-${i}`).value}))};
}

function renderRepurpose(task) {
  repurposeCurrent = task;
  let content = task.content;
  try {
    const draft = JSON.parse(sessionStorage.getItem('variant:' + task.id));
    if (task.status === 'review' && draft?.version === task.version) {content = draft.content; repurposeDirty = true;}
  } catch (_) {}
  const stale = task.source_changed || task.profile_changed;
  const delivery=task.distribution;
  const deliveryNotice=delivery?`<div class="notice">发布进度：${esc(delivery.status==='blocked'?'需要处理':delivery.status)} · ${esc(delivery.message||'已进入发布检查')}${delivery.url?`<a href="${safeLink(delivery.url)}" target="_blank" rel="noopener">打开平台草稿或文章</a>`:""}${delivery.job_id?'<a href="#queue">查看发布队列</a>':''}</div>`:'';
  const notice = deliveryNotice + (typeof modelProvenanceHtml==='function'?modelProvenanceHtml(task.model_provenance):'') + memorySnapshotHtml(task.context?.profile) + `<p class="tip">${esc(task.source_title)} · 母稿 v${task.source_version} · ${esc(task.profile_name)} · ${esc(repurposeLabels[task.status])}</p>${stale ? '<div class="notice">母稿、画像或长期记忆已变化，本稿仍使用启动时的旧快照。可核对后采用，或从母稿重新改编。</div>' : ''}`;
  if (task.status !== 'review') {
    modal(platformNames()[task.platform], notice + `<p>${esc(task.error || (task.status === 'applied' ? '已采用的平台稿可独立修改，母稿保持不变。' : '任务在后台执行，可关闭弹窗后从平台改编列表查看。'))}</p>`, `<button class="button" data-action="close">关闭</button>${['failed', 'interrupted'].includes(task.status) ? `<button class="button" data-action="repurpose-retry" data-id="${task.id}">只重试此平台</button>` : ''}${delivery?.status === "blocked" && !delivery.job_id ? `<button class="button" data-action="repurpose-deliver" data-id="${task.id}">连接后继续送稿</button>` : ""}${task.applied_post_id ? `<a class="button primary" href="#edit/${task.applied_post_id}">打开平台稿</a>` : `<button class="button" data-action="repurpose-show" data-id="${task.id}">刷新状态</button>`}`);
    return;
  }
  const visual=task.visuals||{pages:[],status:'not_started'};
  const gallery=`<p>${visual.mode==='ai'?'AI 图片':'排版图卡'} · ${visual.complete||0}/${visual.total||0} 张就绪</p><div class="platform-gallery">${visual.pages.map(p=>p.url?`<figure><img src="${esc(p.url)}" alt="${esc(p.title)}"><figcaption>${esc(p.title)}</figcaption>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(p.model_provenance):''}</figure>`:`<section><p>${esc(p.error||'图片正在准备…')}</p>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(p.model_provenance):''}</section>`).join('')}</div><button class="button small" data-action="repurpose-show" data-id="${task.id}">刷新图片状态</button>${['failed','not_started'].includes(visual.status)?'<button class="button small" data-action="repurpose-retry-images">重试未完成图片</button>':''}`;
  modal('审阅 ' + platformNames()[task.platform], notice + gallery + `<p class="tip">模型输出需核对事实与引用。编辑先保存在此标签页，点击“保存修改”后才存入工作台。保存修改后会重新生成对应图片。图文齐全后点击采用按钮，保存为独立平台稿并执行发布检查；CSDN、掘金当前自动送草稿，公开发表需在平台完成；连接或图片有问题时保留稿件并列出原因，未发送不能标为已发表。</p><label>标题<input id="variant-title" maxlength="${task.platform === 'xiaohongshu' ? 20 : 150}" value="${esc(content.title)}"></label><label>摘要<textarea id="variant-summary" rows="2" maxlength="300">${esc(content.summary)}</textarea></label><label>标签<input id="variant-tags" value="${esc(content.tags.join(', '))}"></label>${task.platform === 'xiaohongshu' ? `<label>独立配文<textarea id="variant-caption" rows="4" maxlength="5000">${esc(content.caption)}</textarea></label>${content.pages.map((p, i) => `<section class="panel panel-body spaced"><h3>第 ${i + 1} 页${i === 0 ? ' · 封面' : ''}</h3><label>页标题<input id="variant-page-title-${i}" maxlength="40" value="${esc(p.title)}"></label><label>页文案<textarea id="variant-page-text-${i}" maxlength="220" rows="3">${esc(p.text)}</textarea></label><label>视觉安排<textarea id="variant-page-visual-${i}" maxlength="1000" rows="2">${esc(p.visual)}</textarea></label></section>`).join('')}` : `<label>正文<textarea id="variant-body" maxlength="100000" rows="14">${esc(content.body)}</textarea></label>`}${stale ? '<label class="check-label"><input type="checkbox" id="variant-ack">已核对，采用基于旧快照的平台稿</label>' : ''}<p id="variant-save-status" role="status">${repurposeDirty ? '已恢复此标签页的未保存编辑' : '已保存 · v' + task.version}</p>`, `<button class="button" data-action="repurpose-discard">关闭并放弃未保存修改</button><button class="button" data-action="repurpose-save">保存修改</button><button class="button primary" data-action="repurpose-apply">${platformApplyLabel(task.platform)}</button>`);
  mountRepurposeAssistant();
}

function mountRepurposeAssistant() {
  if(repurposeCurrent?.status==='review'&&$('#variant-title'))assistantWrap($('#modal-content .modal-body'),'variant',repurposeCurrent.id);
}

async function handleRepurposeAction(button) {
  const action = button.dataset.action;
  if (action === 'close' && repurposeDirty) throw new Error('请先保存平台稿，或选择“关闭并放弃未保存修改”');
  if (!action.startsWith('repurpose-')) return false;
  if (action === 'repurpose-open') {
    await saveEditor(true);
    if (dirty) throw new Error('请先保存当前稿件');
    if (editor.source_post_id) location.hash = '#repurpose/' + editor.source_post_id;
    else openRepurposeCreate(editor);
  } else if (action === 'repurpose-continue') {
    const post=state.posts.find(p=>p.id===$('#repurpose-source').value);
    if(!post)throw new Error('请先选择母稿');
    const result=await api('/repurpose/continue',{post_id:post.id,post_version:post.version,request_id:crypto.randomUUID().replaceAll('-','')});
    location.hash='#repurpose/'+post.id;
    await drawRepurpose();
    toast(result.created_platforms.length?'缺少的平台稿已开始生成，其余继续现有任务':'已打开现有平台稿，已发布平台自动跳过');
  } else if (action === 'repurpose-new') {
    openRepurposeCreate(state.posts.find(p => p.id === $('#repurpose-source').value));
  } else if (action === 'repurpose-create') {
    const form = $('#repurpose-create');
    modalBusy = true;
    try {
      await api('/repurpose', {post_id: form.dataset.post, post_version: Number(form.dataset.version), request_id: form.dataset.request,
        visual_mode: $('#repurpose-visual-mode').value, instruction: $('#repurpose-instruction').value, platforms: $$('input[name="repurpose-platform"]:checked').map(p => p.value)});
      const source = form.dataset.post;
      modalBusy = false;
      closeModal();
      location.hash = '#repurpose/' + source;
      if (view === 'repurpose') await drawRepurpose();
      toast('已建立各平台任务，完成后分别审阅');
    } finally {modalBusy = false;}
  } else if (action === 'repurpose-show') {
    if (repurposeDirty) throw new Error('请先保存当前平台稿');
    renderRepurpose(await api('/repurpose/' + button.dataset.id));
  } else if (action === 'repurpose-refresh') {
    await drawRepurpose();
  } else if (action === 'repurpose-deliver') {
    await api('/repurpose/'+button.dataset.id+'/deliver',{});
    renderRepurpose(await api('/repurpose/'+button.dataset.id));
  } else if (action === 'repurpose-retry') {
    renderRepurpose(await api('/repurpose/' + button.dataset.id + '/retry', {}));
    if (view === 'repurpose') await drawRepurpose();
  } else if (action === 'repurpose-retry-images') {
    if(repurposeDirty)throw new Error('请先保存平台文案再重试图片');
    renderRepurpose(await api('/repurpose/'+repurposeCurrent.id+'/retry-images',{version:repurposeCurrent.version}));
  } else if (action === 'repurpose-discard') {
    sessionStorage.removeItem('variant:' + repurposeCurrent.id);
    repurposeDirty = false;
    closeModal();
  } else if (action === 'repurpose-save' || action === 'repurpose-apply') {
    const ack = Boolean($('#variant-ack')?.checked);
    modalBusy = true;
    try {
      // Freeze form input while the write is in flight; never drop later edits.
      $$('#modal-content input, #modal-content textarea').forEach(el => el.disabled = true);
      repurposeCurrent = await api('/repurpose/' + repurposeCurrent.id + '/edit', {version: repurposeCurrent.version, content: repurposeValues()});
      sessionStorage.removeItem('variant:' + repurposeCurrent.id);
      repurposeDirty = false;
      if (action === 'repurpose-apply' && repurposeCurrent.visuals?.status!=='ready') {renderRepurpose(repurposeCurrent);toast('文案已保存，图卡正在更新；完成后再采用整份图文');return;}
      if (action === 'repurpose-apply') {
        const result = await api('/repurpose/' + repurposeCurrent.id + '/apply', {version: repurposeCurrent.version, ack_source_changes: ack, publish_public:true});
        state.posts = [result.post, ...state.posts.filter(p => p.id !== result.post.id)];
        modalBusy = false;
        closeModal();
        if(location.hash.startsWith('#chat/'))await drawChat();
        else location.hash = result.variant.distribution?.job_id ? '#queue' : '#edit/' + result.post.id;
        toast(result.variant.distribution?.message||'图文已采用，发布进度请在队列核对');
      } else {renderRepurpose(repurposeCurrent); toast('平台稿修改已保存');}
    } finally {modalBusy = false; $$('#modal-content input, #modal-content textarea').forEach(el => el.disabled = false);}
  }
  return true;
}

document.addEventListener('input', e => {
  if (repurposeCurrent?.status === 'review' && e.target.id.startsWith('variant-') && e.target.id !== 'variant-ack') {
    repurposeDirty = true;
    try { sessionStorage.setItem('variant:' + repurposeCurrent.id, JSON.stringify({version: repurposeCurrent.version, content: repurposeValues()})); } catch (_) {}
    $('#variant-save-status').textContent = '有未保存的修改 · 暂存在此标签页';
  }
});
