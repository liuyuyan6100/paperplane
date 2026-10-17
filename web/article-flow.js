"use strict";
let articleFlowRequest = 0;
let articleFlowState = null;
let articleFlowLoading = false;
const preparationPrompted=new Set();

function articleFlowHtml(flow) {
  const checks = (flow.intent?.report?.checks || []).filter(c => c.level !== 'pass');
  const blocking = flow.intent?.status === 'waiting' && !flow.job;
  const preparation=flow.preparation;
  const preparing=preparation&&!preparation.source_changed&&['queued','running'].includes(preparation.status);
  const review=preparation&&!preparation.source_changed&&preparation.status==='review';
  let primary = '';
  if (flow.job) primary = `<button class="button primary" data-action="job" data-id="${esc(flow.job.id)}">查看送稿记录 →</button>`;
  else if(preparing)primary='<button class="button" disabled>AI 正在补齐…</button>';
  else if(review)primary='<button class="button primary" data-action="preparation-open">确认 AI 补齐建议 →</button>';
  else if (flow.action === 'workflow' && flow.workflow) primary = `<a class="button primary" href="#workflows/${esc(flow.workflow.id)}">打开创作任务 →</a>`;
  else if (flow.action === 'workflow-open') primary = '<button class="button primary" data-action="workflow-open">生成提纲 →</button>';
  else if (flow.missing.includes('封面')) primary = '<button class="button primary" data-action="illustration-open">补充封面与插图 →</button>';
  else if (flow.missing.length) primary = '<button class="button primary" data-action="flow-metadata">补齐文章信息 →</button>';
  else if (blocking) primary = '<button class="button primary" data-action="save">保存并自动复查 →</button>';
  else primary = '<button class="button primary" data-action="wechat-draft">检查并送入公众号草稿箱 →</button>';
  const stale = [flow.review?.stale ? `审稿基于 v${flow.review.source_version}，请对照当前版本` : '', flow.plans.some(p => p.stale) ? '配图计划基于旧稿，采用前请核对插入位置' : ''].filter(Boolean);
  return `<div class="flow-title"><div><small>本篇下一步 · 当前 v${flow.post_version}</small><h2>${esc(flow.next_step)}</h2></div>${primary}</div>
    <ol class="article-flow-steps" aria-label="创作流程">${['提纲确认','正文采用','审稿与完善','封面与段落插图','检查并送草稿','微信后台发表'].map((s,i)=>`<li><span>${i+1}</span>${s}</li>`).join('')}</ol>
    ${stale.length ? `<p class="tip">${stale.map(esc).join('；')}。<button class="text-link" data-action="review-open">审阅当前稿件</button></p>` : ''}
    ${preparing?'<p class="notice">正在根据正文生成摘要建议、读取已有署名，并核对图片和视频素材。完成后会请你确认，补齐建议不会提前写入文章。</p>':''}
    ${review?'<p class="notice">补齐结果已备好：请确认摘要、署名及素材修正。确认后保存并自动继续检查，通过后送入公众号草稿箱。</p>':''}
    ${checks.length && blocking&&!preparing&&!review ? `<ul class="flow-checks">${checks.map(c=>`<li><strong>${esc(c.label)}</strong>：${esc(c.detail)}</li>`).join('')}</ul>`:''}
    ${blocking?`<p>${preparing||review?'可以先继续编辑；取消会停止本次自动送稿。':'补齐后保存会自动继续。'}<button class="text-link" data-action="wechat-draft-cancel">取消本次自动继续</button> ${!preparing?'<button class="text-link" data-action="preparation-start">'+(preparation?.source_changed?'按当前稿重新补齐':preparation&&['failed','interrupted'].includes(preparation.status)?'重试 AI 补齐':'AI 先补齐')+'</button>':''}${preparation?.error?`<span class="tip">${esc(preparation.error)}</span>`:''}</p>`:''}
    ${flow.job && flow.job.version !== flow.post_version ? '<p class="notice">当前修改尚未同步到原公众号草稿。本版会保留原草稿并阻止重复新建；请在微信后台核对原稿。</p>' : ''}
    <div class="actions"><button class="button small" data-action="review-open">AI 审稿</button><button class="button small" data-action="illustration-open">配图与真实截图</button><button class="button small" data-action="publish">其他渠道与排期</button></div>
    <details class="flow-models"><summary>本篇模型记录</summary>${flow.models.map(modelProvenanceHtml).join('') || '<p class="tip">尚无可关联的文字生成记录；人工编辑不会被标为模型生成。</p>'}<p class="tip">图片模型和原图来源保留在配图工作台的每张候选图中。</p>${modelProvenanceHtml(flow.delivery_model)}</details>
    <p class="tip">检查通过后自动送入公众号草稿箱；公开发表、群发通知和定时发表在微信后台设置。</p>`;
}

async function refreshArticleFlow() {
  const node = document.querySelector('#article-flow');
  if (!node || !editor || articleFlowLoading) return;
  const postId = editor.id, request = ++articleFlowRequest;
  articleFlowLoading = true;
  try {
    const flow = await api('/posts/' + postId + '/flow');
    const preparation=flow.preparation;
    if (request !== articleFlowRequest || !node.isConnected || editor?.id !== postId) return;
    const waitingBefore=node.dataset.waiting==='yes';
    node.dataset.waiting=flow.intent?.status==='waiting'?'yes':'no';
    articleFlowState = flow;
    if(flow.intent?.status==='waiting'&&!waitingBefore){const context=node.closest('details.editor-context');if(context)context.open=true;}
    const html = articleFlowHtml(flow);
    if (node.dataset.rendered !== html) {
      const expanded = node.querySelector('.flow-models')?.open;
      node.innerHTML = html; node.dataset.rendered = html;
      if (expanded) node.querySelector('.flow-models').open = true;
    }
    const dialog=document.querySelector('#preparation-review');
    if(dialog&&preparation?.id===dialog.dataset.preparationId&&preparation.source_changed){dialog.querySelector('[data-preparation-confirm]').disabled=true;dialog.querySelector('[data-preparation-error]').hidden=false;dialog.querySelector('[data-preparation-error]').textContent='文章或素材已变化，请关闭后按当前稿重新补齐；输入内容仍保留在此页。';}
    if(preparation?.status==='review'&&!preparation.source_changed&&!preparationPrompted.has(preparation.id)&&!dirty&&!document.querySelector('dialog[open]')&&!document.activeElement?.matches('input,textarea,select')){
      preparationPrompted.add(preparation.id);setTimeout(()=>{if(editor?.id===postId&&!dirty&&!document.querySelector('dialog[open]'))void openPreparationReview().catch(e=>toast(e.message));},0);
    }
  } catch (error) {
    if (node.isConnected && editor?.id === postId) node.textContent = '流程状态暂不可用：' + error.message;
  } finally { articleFlowLoading = false; }
}

async function requestWechatDraft(cancel = false) {
  const context=document.querySelector('details.editor-context');if(context)context.open=true;
  // Cancel before saving: saving corrected fields can immediately resume delivery.
  if (cancel) {
    const postId = editor.id;
    const current = await api('/posts/' + postId + '/flow');
    if (editor?.id !== postId) return;
    await api('/posts/' + postId + '/wechat-draft/cancel', {version:current.post_version});
    await refreshArticleFlow(); toast('已取消本次自动继续；编辑中的内容仍保留');
    return;
  }
  const saved = await saveEditor(true);
  if (!saved || dirty) throw new Error('请先保存当前修改，再检查送稿');
  const flow = await api('/posts/' + saved.id + '/wechat-draft', {version:saved.version});
  await refresh(false); syncEditorState(); await refreshArticleFlow();
  toast(flow.job ? flow.next_step : flow.preparation&&['queued','running'].includes(flow.preparation.status)?'正在自动补齐，完成后请确认建议':'检查已完成，请确认补齐建议或处理待核实事项');
}

async function openPreparationReview(){
  if(document.querySelector('#preparation-review'))return;
  await refreshArticleFlow();const p=articleFlowState?.preparation;
  if(!p||p.status!=='review'||p.source_changed)throw new Error('补齐建议尚未就绪，或文章已变化，请按当前稿重新准备');
  const result=p.result;
  preparationPrompted.add(p.id);
  const dialog=document.createElement('dialog');dialog.className='preparation-review';dialog.id='preparation-review';dialog.dataset.preparationId=p.id;
  dialog.innerHTML=`<header><h2>确认 AI 补齐建议</h2><button class="button" data-preparation-close>返回编辑</button></header><div class="preparation-review-body"><p>以下内容尚未写入文章。可修改摘要和署名后确认；正文只处理列出的素材引用和画像既有署名入口。</p><label>公众号摘要<textarea data-preparation-summary maxlength="120" rows="3">${esc(result.summary)}</textarea></label><label>作者署名<input data-preparation-author maxlength="16" value="${esc(result.author)}"><small>来源：${esc(result.author_source)}</small></label><h3>图片核对</h3>${result.image_repairs.length?result.image_repairs.map(r=>`<div class="preparation-media"><img src="${esc(r.new)}" alt="${esc(r.alt)}"><div><strong>${esc(r.alt)}</strong><p>${esc(r.reason)}</p><small>恢复本篇已使用的原图，正文位置不变。</small></div></div>`).join(''):'<p>未替换正文图片。</p>'}<h3>视频准备</h3>${result.videos.length?result.videos.map(v=>`<p>${esc(v.title)}：${v.ready?'已自动关联公众号播放器':'仍待核对'}</p>`).join(''):'<p>本次没有需要处理的视频。</p>'}${result.pending.length?`<div class="notice"><strong>仍需你核实</strong><ul>${result.pending.map(v=>`<li>${esc(v)}</li>`).join('')}</ul></div>`:''}${typeof modelProvenanceHtml==='function'?`<details><summary>摘要模型调用信息</summary>${modelProvenanceHtml(p.model_provenance)}</details>`:''}<p role="alert" class="error-inline" data-preparation-error hidden></p></div><footer><button class="button" data-preparation-close>暂不确认</button><button class="button primary" data-preparation-confirm>确认补齐并继续检查</button></footer>`;
  document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.querySelectorAll('[data-preparation-close]').forEach(b=>b.onclick=()=>dialog.close());
  if(result.summary_shortened){const note=document.createElement('details');note.innerHTML='<summary>摘要已按公众号长度限制压缩，查看原始建议</summary><p>'+esc(result.original_summary)+'</p>';dialog.querySelector('[data-preparation-summary]').closest('label').after(note);}
  if(result.footer_added){const note=document.createElement('p');note.className='notice';note.textContent='将补入画像中已设置的文末署名与入口：'+result.footer;dialog.querySelector('[data-preparation-error]').before(note);}
  dialog.querySelector('[data-preparation-confirm]').onclick=async()=>{
    const button=dialog.querySelector('[data-preparation-confirm]'),error=dialog.querySelector('[data-preparation-error]');button.disabled=true;
    try{
      if(editor?.id!==p.post_id)throw new Error('当前文章已切换，请返回原文章确认这份建议');
      if(dirty)throw new Error('编辑区有未保存修改，请先返回保存，再按当前稿重新补齐');
      await api('/preparations/'+p.id+'/confirm',{version:p.source_version,summary:dialog.querySelector('[data-preparation-summary]').value,author:dialog.querySelector('[data-preparation-author]').value});
      const id=p.post_id;dialog.close();await refresh(false);if(editor?.id===id){editor=state.posts.find(row=>row.id===id);drawEditor();}toast('已保存确认的补齐内容，正在继续检查');
    }catch(e){error.hidden=false;error.textContent=e.message;button.disabled=false;}
  };
  dialog.showModal();
}
document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-action="preparation-open"],[data-action="preparation-start"]');if(!button)return;
  try{
    if(button.dataset.action==='preparation-open'){await openPreparationReview();return;}
    button.disabled=true;const saved=await saveEditor(true);if(!saved||dirty)throw new Error('请先保存当前修改');
    await api('/posts/'+saved.id+'/preparation',{version:saved.version,retry:true});await refreshArticleFlow();
  }catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
});
