"use strict";
let illustrationPlan = null, illustrationDirty = false, illustrationTimer = null, illustrationSequence = 0, illustrationArchived = false, illustrationArchiving = false, illustrationPlanningRequest = false, illustrationSaving = false;
let illustrationUploading = false, illustrationAdoptionPreview = null, illustrationPreviewSequence = 0;
const illustrationLabels = {review:'待审阅', queued:'排队中', running:'生成中', failed:'失败', interrupted:'已中断'};
const illustrationSlots = {cover:'文章封面', body:'正文插画（按章节或段落插入）', xhs:'小红书图集'};

function illustrationModelHtml(value) {
  return typeof modelProvenanceHtml==='function' ? modelProvenanceHtml(value) : '';
}

function illustrationPositionOptions(selected) {
  const options=illustrationPlan?.placement_options||[],exists=options.some(p=>p.id===selected);
  return `<option value="" ${!selected?'selected':''}>请选择章节末或段落后</option>`+
    (selected==='legacy_end'?'<option value="legacy_end" selected>旧计划：追加到文章末尾（可能在署名之后）</option>':'')+
    (selected&&selected!=='legacy_end'&&!exists?`<option value="${esc(selected)}" selected>原位置已变化，请重新选择</option>`:'')+
    options.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.label)}</option>`).join('');
}

function illustrationPageHtml(p,i) {
  const placement=p.placement??(p.slot==='body'?'legacy_end':''),disabled=illustrationPlan.archived_at?'disabled':'';
  return `<section class="panel panel-body spaced"><h3>第 ${i+1} 页</h3>
    <label>图标题<input id="illustration-title-${i}" data-illustration-field ${disabled} maxlength="80" value="${esc(p.title)}"></label>
    <label>图片用途<select id="illustration-slot-${i}" data-illustration-field ${disabled}>${Object.entries(illustrationSlots).map(([v,label])=>`<option value="${v}" ${p.slot===v?'selected':''}>${label}</option>`).join('')}</select></label>
    <div id="illustration-position-wrap-${i}" ${p.slot==='body'?'':'hidden'}><label>插入位置<select id="illustration-position-${i}" data-illustration-field ${disabled}>${illustrationPositionOptions(placement)}</select></label><small>按当前正文定位；对应段落变更时会要求重新选择，不会自动追加到文末。</small></div>
    <label>图片说明（选填，由你确认）<textarea id="illustration-caption-${i}" data-illustration-field ${disabled} maxlength="300" rows="2" placeholder="真实截图说明由你填写；不自动补写未验证的结果">${esc(p.caption||'')}</textarea></label>
    <label>比例<select id="illustration-aspect-${i}" data-illustration-field ${disabled}>${[['landscape','横版 3:2'],['portrait','竖版（小红书交付 3:4）'],['square','方形 1:1']].map(([v,label])=>`<option value="${v}" ${p.aspect===v?'selected':''}>${label}</option>`).join('')}</select><small>比例用于 AI 候选图；上传原图保留原始尺寸与内容。</small></label>
    <label>需要出现在 AI 图中的文字<textarea id="illustration-text-${i}" data-illustration-field ${disabled} maxlength="500" rows="3">${esc(p.text)}</textarea></label>
    <label>AI 画面与提示词<textarea id="illustration-prompt-${i}" data-illustration-field ${disabled} maxlength="5000" rows="5">${esc(p.prompt)}</textarea></label>
    ${illustrationModelHtml(illustrationPlan.next_models?.[p.slot==='cover'?'cover':'illustration'])}
    ${!illustrationPlan.archived_at?`<div class="actions"><button class="button" data-action="illustration-generate" data-index="${i}">生成本页候选图</button><button class="button" data-action="illustration-upload-original" data-index="${i}">上传真实截图 / 原图</button></div><small>上传后预览位置并采用，保留原图，不调用模型或重绘。</small>`:''}</section>`;
}

function illustrationBusy(plan) {
  return ['queued','running'].includes(plan.status) || (plan.candidates || []).some(c=>['queued','running'].includes(c.status));
}

function updateIllustrationPlanState() {
  const plan=illustrationPlan;if(!plan || !$('#illustration-plan'))return;
  const archived=!!plan.archived_at, planning=illustrationPlanningRequest || ['queued','running'].includes(plan.status);
  $('#illustration-save-status').textContent=(illustrationDirty?'有未保存的配图计划':'已保存 · v'+plan.version)+' · '+(archived?'已归档':illustrationLabels[plan.status]||plan.status);
  const error=$('#illustration-plan-error');error.textContent=plan.error||'';error.hidden=!plan.error;
  const hint=$('#illustration-plan-hint');
  hint.textContent=archived?'':planning?'AI 正在完善配图计划，请等待完成后审阅，再生成候选图；无需重复保存或再次完善。':['failed','interrupted'].includes(plan.status)?'本次完善未完成。可以重试 AI 完善配图计划，或手动修改并保存有效计划，再生成候选图。':illustrationDirty?'请先保存当前修改，再生成候选图。':'';
  hint.hidden=!hint.textContent;
  document.querySelectorAll('[data-illustration-field]').forEach(node=>node.disabled=archived||planning||illustrationArchiving||illustrationUploading);
  document.querySelectorAll('#main [data-action]').forEach(button=>{
    const action=button.dataset.action;
    if(['illustration-save','illustration-add-page','illustration-remove-page','illustration-improve'].includes(action)){
      button.disabled=archived||planning||illustrationArchiving||illustrationSaving||illustrationUploading||(action==='illustration-improve'&&illustrationDirty);
      button.title=planning?'请等待 AI 完善配图计划结束':action==='illustration-improve'&&illustrationDirty?'请先保存当前修改':'';
      if(action==='illustration-improve')button.textContent=['failed','interrupted'].includes(plan.status)?'重试 AI 完善配图计划':'AI 完善配图计划';
    }
    if(['illustration-generate','illustration-revise','illustration-confirm-revise','illustration-upload-original'].includes(action)){
      button.disabled=archived||planning||illustrationArchiving||illustrationSaving||illustrationUploading||illustrationDirty||plan.status!=='review';
      button.title=planning?'请等待 AI 完善配图计划结束':plan.status!=='review'?'请重试完善，或手动修改并保存有效计划':illustrationDirty?'请先保存当前修改':'';
    }
  });
  const archiveButton=document.querySelector('[data-action="illustration-archive"]');
  if(archiveButton){archiveButton.disabled=illustrationArchiving||!archived&&(planning||illustrationBusy(plan));archiveButton.title=archiveButton.disabled?'生成中的计划需等待任务结束后再归档':'';}
}

function illustrationArchiveButton(plan) {
  const archived=!!plan.archived_at, busy=!archived&&illustrationBusy(plan);
  return `<button class="button" data-action="illustration-archive" data-id="${esc(plan.id)}" data-version="${plan.version}" data-archived="${!archived}" ${busy?'disabled title="生成中的计划需等待任务结束后再归档"':''}>${archived?'恢复计划':'归档计划'}</button>`;
}

function illustrationSettings(s) {
  if (s.use_shared) return `<div class="panel panel-body"><b>图片使用统一模型连接</b><p class="tip spaced">封面与插图分别使用设置页所选的图片模型。</p><a class="text-link" href="#settings">前往设置 → 模型与能力</a></div>`;
  return `<details class="panel panel-body"><summary>图片模型设置 · ${s.configured ? '已填写，权限需实际验证' : '待配置'}</summary><p class="tip spaced">独立使用图片模型，文本模型不会用于生图。每次生成一张，可能产生费用；失败不自动重复请求。</p><label>图片接口地址<input id="image-base" data-image-setting value="${esc(s.base_url)}" placeholder="https://…/v1"></label><label>图片 API Key<input id="image-key" data-image-setting type="password" autocomplete="new-password" placeholder="${s.key_set ? '已配置，留空保留' : '填写图片服务密钥'}"></label><label>图片模型<input id="image-model" data-image-setting value="${esc(s.model)}" list="image-models" placeholder="填写该接口支持的图片模型"><datalist id="image-models"></datalist></label><label>参考图能力<select id="image-references" data-image-setting><option value="edits" ${s.references==='edits'?'selected':''}>支持 images/edits（真实参考图和定向改图）</option><option value="none" ${s.references==='none'?'selected':''}>仅 images/generations（文生图）</option></select></label><div class="actions"><button class="button" data-action="illustration-models">获取模型列表</button><button class="button primary" data-action="illustration-settings">保存图片设置</button></div><p id="image-status" class="tip spaced">模型列表仅用于选名称；第一张生成结果才验证实际出图能力。</p></details>`;
}

function illustrationValues() {
  return illustrationPlan.pages.map((p,i)=>({title:$(`#illustration-title-${i}`).value,text:$(`#illustration-text-${i}`).value,prompt:$(`#illustration-prompt-${i}`).value,slot:$(`#illustration-slot-${i}`).value,aspect:$(`#illustration-aspect-${i}`).value,placement:$(`#illustration-position-${i}`).value,caption:$(`#illustration-caption-${i}`).value}));
}

function drawIllustrationCandidates() {
  if (!$('#illustration-candidates') || !illustrationPlan) return;
  const adopted=new Set(illustrationPlan.adopted.map(a=>a.candidate_id));
  $('#illustration-candidates').innerHTML=illustrationPlan.candidates.length ? illustrationPlan.candidates.slice().reverse().map(c=>{
    const a=illustrationPlan.adopted.find(a=>a.candidate_id===c.id),p=c.context.page,placement=(a?.placement||p.placement)??(p.slot==='body'?'legacy_end':''),caption=a?.caption??p.caption??'',original=c.origin==='original'||c.context.origin==='original';
    const location=p.slot==='body'?(placement==='legacy_end'?'旧计划：追加到文章末尾（可能在署名之后）':illustrationPlan.placement_options?.find(o=>o.id===placement)?.label||'位置已变化或未选择，采用前请重新定位'):illustrationSlots[p.slot];
    return `<article class="panel panel-body" data-illustration-candidate="${esc(c.id)}"><h3>第 ${c.page_index+1} 页 · ${esc(p.title)}</h3><p><span class="tag">${esc(illustrationLabels[c.status])}</span> <span class="tag">${original?'上传原图':'AI 插画'}</span> ${adopted.has(c.id)?'<span class="tag green">当前已采用</span>':''} ${c.plan_changed?'<span class="tag amber">使用旧计划</span>':''}</p>${illustrationModelHtml(c.model_provenance)}<p>位置：${esc(location)}</p>${caption?`<p>图片说明：${esc(caption)}</p>`:''}${c.image_url?`<a href="${esc(c.image_url)}" target="_blank" rel="noopener"><img src="${esc(c.image_url)}" alt="候选配图，需人工检查" class="illustration-candidate-image"></a><p class="tip">${original?'保留上传的原始图片。请自行确认截图内容与图片说明；未进行 AI 重绘。':'打开原图检查中文、关系、裁切和手机可读性，再采用。AI 插画不代表实际操作截图。'}</p>`:''}<p>${esc(c.error)}</p><div class="actions">${illustrationPlan.archived_at?'':c.status==='review'?`<button class="button primary" data-action="illustration-adopt" data-id="${c.id}">${adopted.has(c.id)?'查看 / 调整位置':'预览位置并采用'}</button>${original?'':`<button class="button" data-assistant-image="${c.id}">与 AI 对话修改此图</button>`}`:['failed','interrupted'].includes(c.status)?`<button class="button" data-action="illustration-generate" data-index="${c.page_index}">按已保存计划重新生成此页</button>`:''}</div></article>`;
  }).join('') : '<p class="tip">尚无候选图。可以逐页生成 AI 图片，或上传真实截图，再预览位置并采用。</p>';
}

function illustrationAdoptionValues() {
  return {candidate_id:$('#illustration-preview-update')?.dataset.id,post_version:illustrationPlan.post_version,
    placement:$('#illustration-adopt-position')?.value||'',caption:$('#illustration-adopt-caption')?.value||''};
}

async function updateIllustrationAdoptionPreview() {
  const values=illustrationAdoptionValues(),sequence=++illustrationPreviewSequence;
  illustrationAdoptionPreview=null;
  $('#illustration-confirm-adopt').disabled=true;
  $('#illustration-adopt-preview').textContent='正在预览插入位置…';
  try{
    const result=await api('/illustrations/'+illustrationPlan.id+'/preview',values);
    if(sequence!==illustrationPreviewSequence||!$('#illustration-adopt-preview'))return;
    if(JSON.stringify(values)!==JSON.stringify(illustrationAdoptionValues()))return;
    illustrationAdoptionPreview={...values,post_version:result.post_version};
    $('#illustration-adopt-preview').innerHTML=`<p><b>${esc(result.placement_label)}</b></p><div class="markdown-body" style="max-height:420px;overflow:auto">${result.preview_html}</div>`;
    $('#illustration-confirm-adopt').disabled=false;
  }catch(e){if(sequence===illustrationPreviewSequence&&$('#illustration-adopt-preview'))$('#illustration-adopt-preview').textContent=e.message;}
}

async function uploadIllustrationOriginal(index) {
  if(illustrationDirty)throw new Error('请先保存计划与图片说明');
  const planId=illustrationPlan.id,version=illustrationPlan.version,input=document.createElement('input');
  input.type='file';input.accept='image/png,image/jpeg,image/gif,image/webp';
  input.onchange=async()=>{
    const file=input.files?.[0];if(!file)return;
    try{
      if(file.size>5000000)throw new Error('图片请控制在 5 MB 以内，原图不会自动压缩或重绘');
      if(illustrationPlan?.id!==planId||illustrationDirty||illustrationPlan.version!==version)throw new Error('计划已变化，请重新选择上传页面');
      illustrationUploading=true;updateIllustrationPlanState();toast('正在上传原图，未调用模型');
      const asset=await api('/media',{data:await fileToBase64(file),title:file.name.slice(0,150),collection:'文章配图原图',origin:'upload'});
      const result=await api('/illustrations/'+planId+'/import_original',{version,page_index:index,image_url:asset.url,request_id:crypto.randomUUID().replaceAll('-','')});
      illustrationUploading=false;
      if(illustrationPlan?.id===planId){await drawIllustrations();document.querySelector(`[data-illustration-candidate="${result.candidates[result.candidates.length-1].id}"]`)?.scrollIntoView({block:'center'});}
      toast('原图已加入候选，请预览位置并采用');
    }catch(e){toast(e.message);}finally{illustrationUploading=false;updateIllustrationPlanState();}
  };
  input.click();
}

async function drawIllustrations() {
  if (illustrationArchiving || illustrationDirty && ($('#illustration-plan') || $('#image-base'))) return;
  clearTimeout(illustrationTimer);
  const sequence=++illustrationSequence,id=location.hash.split('/')[1];
  if (id && /^[a-f0-9]{32}$/.test(id)) {
    const plan=await api('/illustrations/'+id);
    if (view!=='illustrations'||sequence!==illustrationSequence) return;
    illustrationPlan=plan; illustrationDirty=false;
    let pages=plan.pages;
    try {const draft=JSON.parse(sessionStorage.getItem('illustration:'+id));if(!plan.archived_at&&draft?.version===plan.version){pages=draft.pages;illustrationDirty=true;}}catch(_){}
    illustrationPlan.pages=pages;
    const profile=plan.context.profile;
    $('#main').innerHTML=heading('配图工作台',`${plan.context.post.title} · 正文 v${plan.source_version} · ${profile?.name||'未选画像'}`,`<button class="button" data-action="illustration-back">返回列表</button><a class="button" href="#edit/${plan.post_id}">打开文章</a>${illustrationArchiveButton(plan)}${!plan.archived_at?'<button class="button primary" data-action="illustration-save">保存计划</button>':''}`)+
      `<section id="illustration-plan" class="panel panel-body"><p id="illustration-save-status" role="status">${illustrationDirty?'已恢复本标签页的未保存计划':'已保存 · v'+plan.version} · ${esc(plan.archived_at?'已归档':illustrationLabels[plan.status])}</p>${plan.archived_at?'<div class="notice blue">本计划已归档，图片、采用记录和文章均保留。恢复后可继续编辑。</div>':''}<p id="illustration-plan-error" role="alert">${esc(plan.error)}</p><p id="illustration-plan-hint" class="notice blue" role="status"></p>${illustrationModelHtml(plan.model_provenance)}${illustrationModelHtml(plan.next_models?.illustration_plan)}${memorySnapshotHtml(profile)}${plan.source_changed||plan.profile_changed?'<div class="notice">正文或画像已变化，当前任务保留原快照。采用旧图时会要求核对。</div>':''}<p class="tip">本计划使用创建时的画像与实际参考图。已有图片不会被生成结果自动替换；采用文章封面会替换当前封面，正文插画按选定章节或段落插入，旧计划的文末位置会明确标注；上传原图不经 AI 重绘。小红书图按页序排在原图片前。</p><div class="illustration-reference-grid">${plan.context.references.map(r=>`<img src="${esc(r.url)}" alt="实际风格参考图" class="illustration-reference-image">`).join('')}</div><p class="tip">视觉风格：${esc(profile?.visual_style||'尚未填写，可在画像管理设置')}</p>${!plan.archived_at?'<div class="actions"><button class="button" data-action="illustration-improve">AI 完善配图计划</button><button class="button" data-action="illustration-add-page">＋ 添加一页</button><button class="button" data-action="illustration-remove-page">移除末页</button></div>':''}<div id="illustration-pages">${pages.map(illustrationPageHtml).join('')}</div></section><h2 class="spaced">候选图片</h2><div id="illustration-candidates" class="cards"></div>`;
    assistantWrap($('#main'),'plan',plan.id);drawIllustrationCandidates();updateIllustrationPlanState();
    async function poll(){
      try{const next=await api('/illustrations/'+id);if(view!=='illustrations'||sequence!==illustrationSequence)return;
        if((next.version!==illustrationPlan.version||next.archived_at!==illustrationPlan.archived_at)&&!illustrationDirty){illustrationPlan=next;await drawIllustrations();return;}
        illustrationPlan={...illustrationPlan,...next,...(illustrationDirty?{pages:illustrationPlan.pages,version:illustrationPlan.version}:{})};drawIllustrationCandidates();updateIllustrationPlanState();
        if(next.status==='running'||next.status==='queued'||next.candidates.some(c=>['running','queued'].includes(c.status)))illustrationTimer=setTimeout(poll,1800);
      }catch(e){toast(e.message);}
    }
    if(['queued','running'].includes(plan.status)||plan.candidates.some(c=>['queued','running'].includes(c.status)))illustrationTimer=setTimeout(poll,1800);
  } else {
    illustrationPlan=null;illustrationDirty=false;
    const [settings,result]=await Promise.all([api('/illustrations/settings'),api('/illustrations'+(illustrationArchived?'?archived=1':''))]);
    if(view!=='illustrations'||sequence!==illustrationSequence)return;
    const posts=state.posts.filter(p=>!p.archived&&p.body.trim()&&p.stage!=='idea');
    $('#main').innerHTML=heading('配图工作台','从已确认内容规划封面、正文插画和小红书图集。')+illustrationSettings(settings)+`<section class="panel panel-body spaced"><label>选择已保存的文章<select id="illustration-source"><option value="">选择文章</option>${posts.map(p=>`<option value="${p.id}">${esc(p.title)} · v${p.version}</option>`).join('')}</select></label><button class="button primary" data-action="illustration-create">建立配图计划</button></section><section class="spaced"><div class="actions" role="group" aria-label="配图计划筛选"><button class="button ${!illustrationArchived?'primary':''}" data-action="illustration-list-active" aria-pressed="${!illustrationArchived}">进行中</button><button class="button ${illustrationArchived?'primary':''}" data-action="illustration-list-archived" aria-pressed="${illustrationArchived}">已归档</button></div>${result.plans.map(p=>`<article class="workflow-history-row"><div><h3>${esc(p.title)}</h3><p>${esc(p.archived_at?'已归档':illustrationLabels[p.status])} · 计划 v${p.version}</p></div><div class="actions"><a class="button" href="#illustrations/${p.id}">打开计划与候选图</a>${illustrationArchiveButton(p)}</div></article>`).join('')||empty(illustrationArchived?'尚无已归档计划':'尚无进行中的配图计划',illustrationArchived?'归档只收起计划，图片和采用历史仍保留。':'可以先规划内容，接通图片接口后再生成。')}</section>`;
  }
}

function illustrationDraft() {
  if(illustrationArchiving || illustrationPlan?.archived_at)return;
  illustrationDirty=true;
  if(illustrationPlan&&$('#illustration-plan')){
    sessionStorage.setItem('illustration:'+illustrationPlan.id,JSON.stringify({version:illustrationPlan.version,pages:illustrationValues()}));
    updateIllustrationPlanState();
  }
}

async function handleIllustrationAction(button) {
  const action=button.dataset.action;
  if(!action.startsWith('illustration-')) return false;
  if(illustrationArchiving)throw new Error('计划归档状态正在保存，请稍候');
  const editingActions=['illustration-save','illustration-improve','illustration-add-page','illustration-remove-page','illustration-generate','illustration-adopt','illustration-confirm-adopt','illustration-revise','illustration-confirm-revise','illustration-upload-original'];
  if(illustrationPlan?.archived_at&&editingActions.includes(action))throw new Error('计划已归档，请先恢复再编辑或采用图片');
  const planActions=['illustration-save','illustration-improve','illustration-add-page','illustration-remove-page','illustration-generate','illustration-revise','illustration-confirm-revise','illustration-upload-original'];
  if((illustrationSaving||illustrationUploading)&&planActions.includes(action))throw new Error('配图计划或原图正在保存，请稍候');
  if(illustrationPlan&&planActions.includes(action)&&(illustrationPlanningRequest||['queued','running'].includes(illustrationPlan.status)))throw new Error('AI 正在完善配图计划，请等待完成后再操作');
  if(illustrationPlan&&['illustration-generate','illustration-revise','illustration-confirm-revise'].includes(action)&&illustrationPlan.status!=='review')throw new Error('配图计划尚未就绪，请重试 AI 完善，或手动修改并保存有效计划');
  if(action==='illustration-list-active'||action==='illustration-list-archived'){
    if(illustrationDirty)throw new Error('请先保存当前修改，再切换计划列表');
    illustrationArchived=action==='illustration-list-archived';await drawIllustrations();
  } else if(action==='illustration-archive'){
    if(illustrationDirty)throw new Error('请先保存当前修改，再归档或恢复计划');
    const id=button.dataset.id,version=Number(button.dataset.version),archived=button.dataset.archived==='true';
    if(!/^[a-f0-9]{32}$/.test(id)||!Number.isSafeInteger(version)||version<1)throw new Error('计划记录已变化，请重新打开');
    if(archived&&illustrationPlan?.id===id&&illustrationBusy(illustrationPlan))throw new Error('生成中的计划需等待任务结束后再归档');
    // Do not discard a recovered editor draft when archiving from the list.
    if(archived&&!illustrationPlan){
      let draft;
      try{draft=JSON.parse(sessionStorage.getItem('illustration:'+id));}catch(_){}
      if(draft?.version===version&&Array.isArray(draft.pages))throw new Error('请先打开计划，保存未完成的编辑，再归档');
    }
    const controls=[...document.querySelectorAll('#main input,#main textarea,#main select,#main button')].map(node=>[node,node.disabled]);
    illustrationArchiving=true;controls.forEach(([node])=>node.disabled=true);
    try{
      await api('/illustrations/'+id+'/archive',{version,archived});
      // Keep the current list filter. A detail remains visible as a read-only archive.
      illustrationArchiving=false;await drawIllustrations();toast(archived?'计划已归档，图片和采用记录保留':'计划已恢复');
    } finally {
      illustrationArchiving=false;controls.forEach(([node,disabled])=>{if(node.isConnected)node.disabled=disabled;});
    }
  } else if(action==='illustration-open'){
    await saveEditor(true);if(dirty)throw new Error('请先保存文章');
    const plan=await api('/illustrations',{post_id:editor.id,post_version:editor.version});
    location.hash='#illustrations/'+plan.id;
  } else if(action==='illustration-create') {
    const post=state.posts.find(p=>p.id===$('#illustration-source').value);if(!post)throw new Error('请选择文章');
    const plan=await api('/illustrations',{post_id:post.id,post_version:post.version});location.hash='#illustrations/'+plan.id;
  } else if(action==='illustration-settings') {
    await api('/illustrations/settings',{base_url:$('#image-base').value,api_key:$('#image-key').value,model:$('#image-model').value,references:$('#image-references').value});
    illustrationDirty=false;await drawIllustrations();toast('图片设置已保存');
  } else if(action==='illustration-models') {
    const r=await api('/illustrations/models',{base_url:$('#image-base').value,api_key:$('#image-key').value});
    $('#image-models').innerHTML=r.models.map(m=>`<option value="${esc(m)}"></option>`).join('');$('#image-status').textContent=`读取到 ${r.models.length} 个模型；请确认所选模型支持图片，列表不代表实际出图成功。`;
  } else if(action==='illustration-back') {
    if(illustrationDirty){modal('配图计划尚未保存','<p>保存计划后返回，或明确放弃此次未保存编辑。</p>','<button class="button" data-action="close">继续编辑</button><button class="button" data-action="illustration-discard">放弃修改并返回</button>');}
    else location.hash='#illustrations';
  } else if(action==='illustration-discard') {
    if(illustrationPlan)sessionStorage.removeItem('illustration:'+illustrationPlan.id);illustrationDirty=false;closeModal();location.hash='#illustrations';
  } else if(action==='illustration-save') {
    illustrationSaving=true;updateIllustrationPlanState();
    try{
      const pages=illustrationValues(),saved=await api('/illustrations/'+illustrationPlan.id+'/edit',{version:illustrationPlan.version,pages});
      // Preserve any typing made while the save was in flight.
      const changed=JSON.stringify(illustrationValues())!==JSON.stringify(pages);
      illustrationPlan=saved;illustrationDirty=changed;
      if(changed){illustrationDraft();toast('已保存此前内容，继续输入的部分尚未保存');}
      else{sessionStorage.removeItem('illustration:'+saved.id);await drawIllustrations();toast('计划已保存');}
    }finally{illustrationSaving=false;updateIllustrationPlanState();}
  } else if(action==='illustration-add-page'||action==='illustration-remove-page') {
    if(illustrationPlan.adopted.length)throw new Error('已有采用图片，调整页数请新建计划');
    const pages=illustrationValues();
    if(action==='illustration-add-page'){if(pages.length>=12)throw new Error('最多12页');pages.push({title:'新的一页',text:'',prompt:'依据文章绘制一个具体场景。',slot:'body',aspect:'landscape',placement:'',caption:''});}
    else{if(pages.length<=1)throw new Error('至少保留一页');pages.pop();}
    sessionStorage.setItem('illustration:'+illustrationPlan.id,JSON.stringify({version:illustrationPlan.version,pages}));illustrationDirty=false;await drawIllustrations();
    // Recovered draft has a different page count; values must follow the rendered pages.
    illustrationPlan.pages=pages;
  } else if(action==='illustration-improve') {
    if(illustrationDirty)throw new Error('请先保存计划');
    illustrationPlanningRequest=true;updateIllustrationPlanState();
    try{await api('/illustrations/'+illustrationPlan.id+'/improve',{version:illustrationPlan.version});await drawIllustrations();}
    finally{illustrationPlanningRequest=false;updateIllustrationPlanState();}
  } else if(action==='illustration-generate') {
    if(illustrationDirty)throw new Error('请先保存配图计划');
    await api('/illustrations/'+illustrationPlan.id+'/generate',{version:illustrationPlan.version,page_index:Number(button.dataset.index),request_id:crypto.randomUUID().replaceAll('-','')});await drawIllustrations();
  } else if(action==='illustration-upload-original') {
    await uploadIllustrationOriginal(Number(button.dataset.index));
  } else if(action==='illustration-adopt') {
    if(illustrationDirty)throw new Error('请先保存计划');
    // Read current text anchors and version before showing an adoption proposal.
    illustrationPlan=await api('/illustrations/'+illustrationPlan.id);
    const c=illustrationPlan.candidates.find(c=>c.id===button.dataset.id);
    if(!c||c.status!=='review')throw new Error('候选图片已变化，请重新打开');
    const a=illustrationPlan.adopted.find(a=>a.candidate_id===c.id),p=c.context.page,placement=(a?.placement||p.placement)??(p.slot==='body'?'legacy_end':'');
    illustrationAdoptionPreview=null;
    modal('预览图片位置并采用',`<p>${c.origin==='original'?'上传原图保持原样，请自行确认图片说明。':'请先检查图中文字、技术关系与手机可读性。'}</p>${illustrationModelHtml(c.model_provenance)}<img src="${esc(c.image_url)}" alt="待采用图片" class="illustration-candidate-image">${p.slot==='body'?`<label>插入位置<select id="illustration-adopt-position" data-illustration-adoption-field>${illustrationPositionOptions(placement)}</select></label>`:`<p>${esc(illustrationSlots[p.slot])}。旧文件和修订记录保留。</p>`}<label>图片说明（选填）<textarea id="illustration-adopt-caption" data-illustration-adoption-field maxlength="300" rows="2">${esc(a?.caption??p.caption??'')}</textarea></label>${illustrationPlan.source_changed||illustrationPlan.profile_changed||c.plan_changed?'<label class="check-label"><input id="illustration-ack" type="checkbox">已核对正文、画像或计划变化，仍采用这张旧快照图</label>':''}<button id="illustration-preview-update" class="button" data-action="illustration-preview" data-id="${c.id}">更新位置预览</button><div id="illustration-adopt-preview" role="status" class="spaced"></div>`,'<button class="button" data-action="close">取消</button><button class="button primary" id="illustration-confirm-adopt" data-action="illustration-confirm-adopt" data-id="'+c.id+'" disabled>确认采用到此位置</button>');
    await updateIllustrationAdoptionPreview();
  } else if(action==='illustration-preview') {
    await updateIllustrationAdoptionPreview();
  } else if(action==='illustration-confirm-adopt') {
    if(!illustrationAdoptionPreview||illustrationAdoptionPreview.candidate_id!==button.dataset.id)throw new Error('请先更新位置预览');
    await api('/illustrations/'+illustrationPlan.id+'/adopt',{...illustrationAdoptionPreview,ack_changes:$('#illustration-ack')?.checked||false});closeModal();await refresh(false);await drawIllustrations();toast('图片已采用到预览位置，可以打开文章继续排版');
  } else if(action==='illustration-revise') {
    if(illustrationDirty)throw new Error('请先保存计划');
    modal('定向修改此图','<label>修改要求<textarea id="illustration-revision" rows="4" maxlength="2000" placeholder="例如：扩大标题留白，保留场景和配色"></textarea></label>','<button class="button" data-action="close">取消</button><button class="button primary" data-action="illustration-confirm-revise" data-id="'+button.dataset.id+'">生成新候选图</button>');
  } else if(action==='illustration-confirm-revise') {
    const c=illustrationPlan.candidates.find(c=>c.id===button.dataset.id);
    await api('/illustrations/'+illustrationPlan.id+'/generate',{version:illustrationPlan.version,page_index:c.page_index,parent_id:c.id,instruction:$('#illustration-revision').value,request_id:crypto.randomUUID().replaceAll('-','')});closeModal();await drawIllustrations();
  }
  return true;
}

document.addEventListener('input',e=>{if(e.target.hasAttribute('data-illustration-field')||e.target.hasAttribute('data-image-setting'))illustrationDraft();});
document.addEventListener('change',e=>{if(e.target.hasAttribute('data-illustration-field')||e.target.hasAttribute('data-image-setting'))illustrationDraft();});
for(const type of ['input','change'])document.addEventListener(type,e=>{
  if(e.target.hasAttribute('data-illustration-adoption-field')){
    illustrationAdoptionPreview=null;illustrationPreviewSequence++;
    if($('#illustration-confirm-adopt'))$('#illustration-confirm-adopt').disabled=true;
    if($('#illustration-adopt-preview'))$('#illustration-adopt-preview').textContent='位置或说明已修改，请更新预览后采用。';
  }
  const match=e.target.id?.match(/^illustration-slot-(\d+)$/);
  if(match&&$(`#illustration-position-wrap-${match[1]}`))$(`#illustration-position-wrap-${match[1]}`).hidden=e.target.value!=='body';
});
