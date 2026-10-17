"use strict";
let editorialReview=null,reviewTimer=null,reviewSequence=0,reviewSignature='';
const reviewLabels={queued:'排队中',running:'审稿中',review:'建议待审阅',applied:'已采用所选建议',failed:'失败',interrupted:'已中断'};
const reviewCategories={clarity:'表达',structure:'结构',voice:'画像口吻',evidence:'事实依据',platform:'平台适配'};

function acceptRepairedPost(post){
  state.posts=[post,...state.posts.filter(p=>p.id!==post.id)];
  if(editor?.id===post.id){
    if(dirty){editor.version=post.version;toast('格式已修复；你继续输入的文字仍未保存');}
    else{editor=post;if($('#edit-body'))$('#edit-body').value=post.body;$('#save-state').textContent=`已保存 · v${post.version}`;void preview();}
  }
}
async function drawReviews(){
  clearTimeout(reviewTimer);const sequence=++reviewSequence,id=location.hash.split('/')[1];
  if(id&&/^[a-f0-9]{32}$/.test(id)){
    const r=await api('/reviews/'+id);if(view!=='reviews'||sequence!==reviewSequence)return;
    const signature=JSON.stringify(r),selected=new Set([...document.querySelectorAll('#review-issues input:checked')].map(el=>el.dataset.reviewSelect?'select:'+el.dataset.reviewSelect:'verified:'+el.dataset.reviewVerified));
    const poll=()=>{if(['queued','running','review'].includes(r.status))reviewTimer=setTimeout(()=>{if(view==='reviews'&&location.hash.endsWith('/'+id))void drawReviews().catch(e=>toast(e.message));},2500);};
    if($('#review-issues')&&editorialReview?.id===id&&reviewSignature===signature){poll();return;}
    reviewSignature=signature;
    editorialReview=r;
    $('#main').innerHTML=heading('审稿建议',`${r.context.post.title} · 正文 v${r.source_version} · ${r.context.profile?.name||'未选画像'}`,`<a class="button" href="#reviews">返回列表</a><a class="button" href="#edit/${r.post_id}">打开原稿</a>`)+
      `<section class="panel panel-body"><p><span class="tag">${esc(reviewLabels[r.status])}</span></p><p>${esc(r.error)}</p><p class="tip">审稿依据是此版本正文及画像，不会联网核实事实。建议不是平台审核结论；原稿仅在你采用时修改。</p>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(r.model_provenance):''}${memorySnapshotHtml(r.context.profile)}${r.source_changed?'<div class="notice">正文版本已变化，旧建议保留供比较，不能直接采用。</div><button class="button primary" data-action="review-current">对最新保存稿重新审稿</button>':''}${r.profile_changed?'<div class="notice">画像或长期记忆已变化，此次建议使用创建时的旧快照。</div><label class="check-label"><input id="review-profile-ack" type="checkbox">已核对旧画像建议，仍决定采用</label>':''}<p class="workflow-preserve">${esc(r.result.summary||'等待审稿结果')}</p>${['failed','interrupted'].includes(r.status)?'<button class="button" data-action="review-retry">按原快照重试审稿</button>':''}</section>`+
      `<div id="review-issues" class="spaced">${(r.result.issues||[]).map(i=>`<article class="panel panel-body spaced"><h3>${esc(reviewCategories[i.category])} · ${esc({info:'建议',warning:'注意',critical:'优先处理'}[i.priority])}</h3><p>${esc(i.reason)}</p><label>原句<pre class="review-quote">${esc(i.quote||'未定位具体句子')}</pre></label><label>建议文字<pre class="review-quote">${esc(i.replacement||'需要补证后在编辑器手动处理')}</pre></label>${i.needs_verification?'<p class="tip">此建议涉及事实或需要核实的信息，模型没有完成核验。</p>':''}${i.applicable&&r.status==='review'&&!r.source_changed?`<label class="check-label"><input type="checkbox" data-review-select="${i.id}">选择采用此建议</label>${i.needs_verification?`<label class="check-label"><input type="checkbox" data-review-verified="${i.id}">我已自行核实此项修改的依据</label>`:''}`:'<p class="tip">保留为建议，在原稿编辑器处理；代码、链接、图片和无法唯一定位的句子不直接替换。</p>'}</article>`).join('')||(r.status==='review'?'<p class="tip">本次模型未列出具体修改建议；事实与平台要求仍需自行核对。</p>':'')}</div>`+
      (r.status==='review'&&!r.source_changed?'<button class="button primary spaced" data-action="review-apply">预览并采用所选建议</button>':'');
    if(!r.source_changed)document.querySelectorAll('#review-issues input').forEach(el=>el.checked=selected.has(el.dataset.reviewSelect?'select:'+el.dataset.reviewSelect:'verified:'+el.dataset.reviewVerified));
    poll();
  }else{
    editorialReview=null;const result=await api('/reviews');if(view!=='reviews'||sequence!==reviewSequence)return;
    $('#main').innerHTML=heading('审稿建议','检查具体表达与依据，逐项决定是否修改。')+`<section class="panel panel-body"><label>选择已保存的文章<select id="review-source"><option value="">选择文章</option>${state.posts.filter(p=>!p.archived&&p.body.trim()&&p.stage!=='idea').map(p=>`<option value="${p.id}">${esc(p.title)} · v${p.version}</option>`).join('')}</select></label><button class="button primary" data-action="review-create">开始 AI 审稿</button><div id="review-planned-model"></div><p class="tip">使用已有文本模型；一次请求可能产生费用，不自动重复生成或覆盖文章。</p></section><section class="spaced">${result.reviews.map(r=>`<article class="workflow-history-row"><div><h3>${esc(r.title)}</h3><p>${esc(reviewLabels[r.status])} · 正文 v${r.source_version}</p></div><a class="button" href="#reviews/${r.id}">查看建议</a></article>`).join('')||empty('尚无审稿记录','可从文章编辑器发起审稿。')}</section>`;
    if(typeof renderPlannedModel==='function')void renderPlannedModel($('#review-planned-model'),'review');
  }
}
async function handleReviewAction(button){
  const action=button.dataset.action;if(!action.startsWith('review-'))return false;
  if(action==='review-open'||action==='review-create'){
    let post;
    if(action==='review-open'){await saveEditor(true);if(dirty)throw new Error('请先保存原稿');post=editor;}
    else post=state.posts.find(p=>p.id===$('#review-source').value);
    if(!post)throw new Error('请选择已保存文章');
    const result=await api('/reviews',{post_id:post.id,post_version:post.version,request_id:crypto.randomUUID().replaceAll('-','')});location.hash='#reviews/'+result.id;
  }else if(action==='review-retry'){
    await api('/reviews/'+editorialReview.id+'/retry',{});await drawReviews();
  }else if(action==='review-current'){
    const latest=await api('/reviews/'+editorialReview.id);
    const result=await api('/reviews',{post_id:latest.post_id,post_version:latest.post_version,request_id:crypto.randomUUID().replaceAll('-','')});location.hash='#reviews/'+result.id;
  }else if(action==='review-apply'){
    if(editorialReview.source_changed)throw new Error('正文已变化，旧建议仅供对照，请对最新保存稿重新审稿');
    const selected=[...document.querySelectorAll('[data-review-select]:checked')].map(el=>({id:el.dataset.reviewSelect,verified:document.querySelector(`[data-review-verified="${el.dataset.reviewSelect}"]`)?.checked||false}));
    if(!selected.length)throw new Error('请选择需要采用的建议');
    if(selected.some(s=>editorialReview.result.issues.find(i=>i.id===s.id).needs_verification&&!s.verified))throw new Error('请先核实对应修改，再勾选已核实');
    if(editorialReview.profile_changed&&!$('#review-profile-ack')?.checked)throw new Error('请先核对旧画像建议');
    modal('采用所选审稿建议',`<p>只替换以下原句，保留文章修订，不发起发布。</p>${selected.map(s=>{const i=editorialReview.result.issues.find(i=>i.id===s.id);return `<h4>${esc(reviewCategories[i.category])}</h4><pre class="review-quote">${esc(i.quote)}</pre><p>改为</p><pre class="review-quote">${esc(i.replacement)}</pre>`;}).join('')}`,'<button class="button" data-action="close">继续检查</button><button class="button primary" data-action="review-confirm">确认采用</button>');
    $('#review-confirm-selection')?.remove();const data=document.createElement('input');data.id='review-confirm-selection';data.type='hidden';data.value=JSON.stringify({issues:selected,ack_profile:$('#review-profile-ack')?.checked||false});$('#modal-content').append(data);
  }else if(action==='review-confirm'){
    if(editorialReview.source_changed)throw new Error('正文已变化，旧建议仅供对照，请对最新保存稿重新审稿');
    const selection=JSON.parse($('#review-confirm-selection').value);
    await api('/reviews/'+editorialReview.id+'/apply',{...selection,post_version:editorialReview.post_version});closeModal();await refresh(false);await drawReviews();toast('所选建议已采用，原修订保留');
  }else if(action==='review-format'||action==='review-undo-format'){
    await saveEditor(true);if(dirty)throw new Error('请先保存原稿');
    const result=await api('/posts/'+editor.id+'/format/'+(action==='review-format'?'repair':'undo'),{post_version:editor.version});acceptRepairedPost(result.post);toast(action==='review-format'?(result.repaired?'多余空行已修复，保留最近一次备份':'没有需要自动修复的多余空行'):'修复前正文已恢复');
  }
  return true;
}
