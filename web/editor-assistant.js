"use strict";

const assistantNames = {post:'文章',outline:'提纲',body:'待审正文',variant:'平台稿',plan:'配图计划',image:'当前图片'};
const assistantPanels = new WeakMap();
const assistantLivePanels = new Set();
const assistantImageTargets = new Map();

function assistantHost(kind,id) {
  return `<aside class="editor-assistant panel" data-assistant-kind="${esc(kind)}" data-assistant-id="${esc(id)}" aria-label="当前工作项 AI 对话"></aside>`;
}
function assistantWrap(container,kind,id) {
  if(!container||container.querySelector('.editor-assistant'))return;
  const layout=document.createElement('div');layout.className='assist-layout';
  const content=document.createElement('div');content.className='assist-document';
  while(container.firstChild)content.append(container.firstChild);
  layout.append(content);layout.insertAdjacentHTML('beforeend',assistantHost(kind,id));container.append(layout);
  if(kind==='plan')layout.querySelector('.editor-assistant').id='illustration-assistant';
}
function assistantDraft(panel) {
  const kind=panel.dataset.assistantKind;
  if(kind==='post') {
    if(editor?.id!==panel.dataset.assistantId||!$('#edit-body'))throw new Error('当前文章已切换，请重新打开对话');
    if(($('#edit-profile').value||null)!==editor.profile_id)throw new Error('请先保存账号画像选择，再使用AI修改');
    return {title:$('#edit-title').value,body:$('#edit-body').value};
  }
  if(kind==='outline')return {title:$('#workflow-title').value,body:$('#workflow-outline').value};
  if(kind==='body')return {title:$('#workflow-result-title').value,body:$('#workflow-result-body').value};
  if(kind==='variant')return repurposeValues();
  if(kind==='plan')return {pages:illustrationValues()};
  return assistantPanels.get(panel).data.content;
}
function assistantTextArea(kind) {
  return $({post:'#edit-body',outline:'#workflow-outline',body:'#workflow-result-body',variant:'#variant-body'}[kind]||'__no_editor__');
}
function assistantEqual(a,b) {
  const normalize=v=>Array.isArray(v)?v.map(normalize):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,normalize(v[k])])):v;
  return JSON.stringify(normalize(a))===JSON.stringify(normalize(b));
}
function assistantMessageKey(panel) {return 'assistant-message:'+panel.dataset.assistantKind+':'+panel.dataset.assistantId;}
function assistantStatus(panel,text,error=false) {
  const node=panel.querySelector('[data-assistant-status]');if(node){node.textContent=text;node.classList.toggle('error-inline',error);}
  const info=assistantPanels.get(panel);if(info)info.notice={text,error};
}
function assistantFit(panel) {
  if(!panel.isConnected||innerWidth<=950)return;
  const modal=panel.closest('.modal-body'),rect=panel.getBoundingClientRect();
  const bottom=modal?Math.min(innerHeight,modal.getBoundingClientRect().bottom):innerHeight;
  if(rect.top>=bottom)return;
  const height=Math.max(220,bottom-Math.max(modal?modal.getBoundingClientRect().top:92,rect.top)-16);
  panel.style.setProperty('--assistant-fit-height',Math.min(820,height)+'px');
}
function assistantSelection(panel) {
  const info=assistantPanels.get(panel),area=assistantTextArea(panel.dataset.assistantKind),selection=info?.selection;
  if(!selection||!area)return false;
  if(selection.body===area.value)return !selection.stale;
  const old=[...selection.body],next=[...area.value];let prefix=0,suffix=0;
  while(prefix<old.length&&prefix<next.length&&old[prefix]===next[prefix])prefix++;
  while(suffix<old.length-prefix&&suffix<next.length-prefix&&old[old.length-1-suffix]===next[next.length-1-suffix])suffix++;
  if(!selection.stale){
    if(old.length-suffix<=selection.start){const shift=next.length-old.length;selection.start+=shift;selection.end+=shift;}
    else if(prefix<selection.end)selection.stale=true;
  }
  selection.body=area.value;
  if(next.slice(selection.start,selection.end).join('')!==selection.text)selection.stale=true;
  return !selection.stale;
}
function assistantComposeState(panel) {
  const info=assistantPanels.get(panel);if(!info)return;
  const range=panel.querySelector('[data-assistant-range]'),card=panel.querySelector('[data-assistant-selection]');
  const selected=range.value==='selection',valid=assistantSelection(panel),candidate=info.candidate;
  card.hidden=!selected&&!candidate;
  card.classList.toggle('is-stale',selected&&!valid);
  card.innerHTML=selected?`<strong>${valid?'仅修改选中片段':'选中片段已改动'}</strong><p>${esc(info.selection?.text.slice(0,120)||'请在编辑区选择一段文字')}</p>${!valid?'<small>输入已保留。重新选段，或切回整篇继续修改。</small>':''}${candidate?'<button class="button small" data-assistant-action="range-selection">改用新选区</button>':''}<button class="button ghost small" data-assistant-action="range-whole">改为整篇修改</button>`:candidate?`<p>已选：${esc(candidate.text.slice(0,90))}</p><button class="button small" data-assistant-action="range-selection">仅修改这段</button><button class="button ghost small" data-assistant-action="dismiss-selection">忽略选区</button>`:'';
  const context=panel.querySelector('[data-assistant-context]');
  let unsaved=false;try{unsaved=!!info.data&&!assistantEqual(assistantDraft(panel),info.data.content);}catch(_){}
  context.textContent=(selected?'只改选中片段':panel.dataset.assistantKind==='post'?'针对整篇文章':'针对当前工作项')+(unsaved?' · 包含未保存修改':'');
  panel.querySelector('[data-assistant-reference]').hidden=!info.reference;
  panel.querySelector('[data-assistant-undo]').hidden=!info.undo;
}
function assistantCompare(turn) {
  const replacement=turn.result?.replacement;
  const text=v=>v&&Object.keys(v).every(k=>['title','body'].includes(k))?(v.title||'')+'\n\n'+(v.body||'').replace(/^@\[([^\]\n]{0,120})\]\(\/video\/[a-f0-9]{32}\.mp4\)[ \t]*$/gm,(_,label)=>'【视频：'+(label||'本篇视频')+'】'):JSON.stringify(v,null,2);
  return `<div class="assistant-comparison"><section><h3>修改前</h3><pre>${esc(text(turn.draft))}</pre></section><section><h3>建议稿</h3><pre>${esc(text(replacement))}</pre></section></div>`;
}
function assistantRender(panel,data) {
  const info=assistantPanels.get(panel);if(!info||!panel.isConnected)return;
  info.data=data;
  panel.querySelector('[data-assistant-title]').textContent=assistantNames[data.kind]+' · AI 对话';
  panel.querySelector('[data-assistant-subtitle]').textContent=data.title;
  let videoContext=panel.querySelector('[data-assistant-videos]');if(!videoContext){videoContext=document.createElement('p');videoContext.dataset.assistantVideos='';videoContext.className='tip assistant-video-context';panel.querySelector('.assistant-top-context details').append(videoContext);}videoContext.hidden=!data.videos?.length;videoContext.textContent=data.videos?.length?'本篇视频已提供给AI（依据素材说明）：'+data.videos.map(v=>v.title).join('、'):'';
  const busy=data.turns.some(t=>['queued','running'].includes(t.status)||['queued','running'].includes(t.candidate?.status));
  const learningBusy=data.turns.some(t=>(t.learning||[]).some(e=>['queued','running'].includes(e.status)));
  panel.querySelector('[data-assistant-send]').disabled=busy||!data.editable||info.sending;
  panel.querySelector('[data-assistant-mode]').hidden=data.kind==='image';
  panel.querySelector('[data-assistant-scope]').hidden=!assistantTextArea(data.kind);
  panel.querySelector('[data-assistant-back]').hidden=data.kind!=='image';
  const history=panel.querySelector('[data-assistant-history]');
  const wasBottom=history.scrollTop+history.clientHeight>=history.scrollHeight-24;
  history.innerHTML=data.turns.length?data.turns.map(t=>{
    const r=t.result||{},replacement=r.replacement;
    const preview=replacement?(replacement.body||JSON.stringify(replacement,null,2)):'';
    const canAdopt=!!replacement&&t.status==='review'&&!t.source_changed;
    return `<article class="assistant-turn" data-assistant-turn="${esc(t.id)}"><p class="assistant-user">${esc(t.message)}</p><p class="workflow-preserve">${esc(t.error||r.reply||({queued:'正在排队…',running:'AI 正在处理…',interrupted:'处理已中断，原内容保留。'}[t.status]||''))}</p>
      ${t.source_changed?'<p class="tip">这是此前版本的建议，可参考它继续修改当前稿。</p>':''}
      ${replacement?`<div class="assistant-result-actions"><button class="button small" data-assistant-action="read" data-id="${esc(t.id)}">展开阅读 / 对照</button>${canAdopt?`<button class="button primary small" data-assistant-action="accept" data-id="${esc(t.id)}">应用修改</button>`:''}<button class="button small" data-assistant-action="refine" data-id="${esc(t.id)}">${t.source_changed?'基于当前稿继续改':'继续调整'}</button>${t.status==='accepted'?'<span class="tip">已确认建议</span>':''}</div><details><summary>查看修改对照</summary>${assistantCompare(t)}</details>`:''}
      ${(t.learning||[]).map(e=>`<p class="tip">画像学习：${esc(e.error||e.result?.note||({queued:'正在等待整理这条长期反馈',running:'正在整理反馈',completed:'反馈已整理'}[e.status]||e.status))}</p>${(e.memories||[]).map(m=>`<p class="tip">${m.status==='accepted'?'已记住：':'待核对：'}${esc(m.content)}</p>`).join('')}`).join('')}
      ${t.candidate?`<p>${esc(illustrationLabels[t.candidate.status]||t.candidate.status)} ${esc(t.candidate.error||'')}</p>${t.candidate.image_url?`<img class="assistant-image" src="${esc(t.candidate.image_url)}" alt="本次修改生成的新候选图片">`:''}${t.candidate.status==='review'?`<div class="actions"><button class="button small" data-assistant-action="adopt-image" data-id="${esc(r.candidate_id)}" data-plan="${esc(r.plan_id)}">预览位置并采用</button><button class="button small" data-assistant-action="continue-image" data-id="${esc(r.candidate_id)}">继续修改这张</button></div>`:''}`:''}
      ${['failed','interrupted'].includes(t.status)&&data.kind!=='image'?`<button class="button small" data-assistant-action="retry" data-id="${esc(t.id)}">按原上下文重试</button>`:''}
      ${typeof modelProvenanceHtml==='function'?`<details class="assistant-call-info"><summary>模型调用信息</summary>${modelProvenanceHtml(t.model_provenance)}</details>`:''}</article>`;
  }).join(''):'<p class="tip">可以直接说“结尾补上博客入口”“把这段讲清楚”。本工作项的对话会保留，切换后再回来可继续。</p>';
  if(wasBottom)history.scrollTop=history.scrollHeight;
  panel.querySelector('[data-assistant-model]').innerHTML=typeof modelProvenanceHtml==='function'?modelProvenanceHtml(data.planned_model):'';
  if(!data.editable)assistantStatus(panel,'当前版本不可编辑，请打开待确认提纲、待审正文或文章编辑器。');
  else if(busy)assistantStatus(panel,'AI 正在处理，结果会保留，可以继续编辑文章。');
  else if(!info.sending&&(!info.notice||info.notice.text.startsWith('AI 正在处理')))assistantStatus(panel,'建议可预览后应用，应用后仍需保存。');
  assistantComposeState(panel);
  requestAnimationFrame(()=>assistantFit(panel));
  clearTimeout(info.timer);
  if(busy||learningBusy)info.timer=setTimeout(()=>assistantRefresh(panel),1800);
}
async function assistantRefresh(panel) {
  const info=assistantPanels.get(panel);if(!info||!panel.isConnected)return;
  const key=panel.dataset.assistantKind+'/'+panel.dataset.assistantId;
  try{
    const data=await api('/editor-assistant/'+key);
    if(panel.isConnected&&key===panel.dataset.assistantKind+'/'+panel.dataset.assistantId)assistantRender(panel,data);
  }catch(e){if(panel.isConnected)assistantStatus(panel,e.message,true);}
}
function assistantMount(panel) {
  if(assistantPanels.has(panel))return;
  const baseKind=panel.dataset.assistantKind,baseId=panel.dataset.assistantId;
  if(baseKind==='plan'&&assistantImageTargets.has(baseId)){
    panel.dataset.assistantKind='image';panel.dataset.assistantId=assistantImageTargets.get(baseId);
  }
  const info={baseKind,baseId,data:null,sending:false,selection:null,timer:null};assistantPanels.set(panel,info);
  assistantLivePanels.add(panel);
  const modalBody=panel.closest('.modal-body');
  if(modalBody){info.resize=new ResizeObserver(()=>{panel.style.setProperty('--assistant-dialog-height',Math.max(180,modalBody.clientHeight-66)+'px');assistantFit(panel);});info.resize.observe(modalBody);}
  panel.innerHTML=`<div class="panel-head"><h2 data-assistant-title>AI 对话</h2><button class="button ghost small" data-assistant-action="refresh">刷新</button></div><div class="assistant-top-context"><p class="tip" data-assistant-subtitle></p><details><summary>本篇素材与模型</summary><div data-assistant-model></div></details><button class="button small" data-assistant-back data-assistant-action="back" hidden>返回配图计划对话</button></div><div class="assistant-history" data-assistant-history aria-live="polite"></div><div class="assistant-compose"><div class="assistant-compose-toolbar"><select data-assistant-mode aria-label="处理方式"><option value="edit">帮我修改</option><option value="discuss">只讨论</option></select><span data-assistant-context></span></div><select data-assistant-range hidden aria-label="修改范围"><option value="whole">当前工作项</option><option value="selection">编辑区选中的片段</option></select><span data-assistant-scope hidden></span><div data-assistant-selection class="assistant-selection" hidden></div><div class="assistant-reference" data-assistant-reference hidden>参考上次建议，继续改当前内容<button class="button ghost small" data-assistant-action="clear-reference">取消参考</button></div><label class="sr-only" for="assistant-message-${esc(baseKind)}-${esc(baseId)}">对 AI 说</label><textarea id="assistant-message-${esc(baseKind)}-${esc(baseId)}" data-assistant-message rows="3" maxlength="4000" placeholder="直接说想怎么改，例如：给术语加角标，文末统一解释。"></textarea><p data-assistant-status role="status" class="tip"></p><div class="assistant-send-row"><button class="button small" data-assistant-undo data-assistant-action="undo" hidden>撤销上次应用</button><small>Ctrl / ⌘ + Enter 发送</small><button class="button primary" data-assistant-send data-assistant-action="send" disabled>发送</button></div></div>`;
  try{panel.querySelector('[data-assistant-message]').value=sessionStorage.getItem(assistantMessageKey(panel))||'';}catch(_){}
  void assistantRefresh(panel);
}
function assistantSwitch(panel,kind,id) {
  const info=assistantPanels.get(panel);clearTimeout(info.timer);
  panel.dataset.assistantKind=kind;panel.dataset.assistantId=id;info.data=null;info.selection=null;info.candidate=null;info.reference=null;info.undo=null;info.notice=null;
  panel.querySelector('[data-assistant-range]').value='whole';assistantComposeState(panel);
  panel.querySelector('[data-assistant-history]').textContent='正在读取此工作项的对话…';
  panel.querySelector('[data-assistant-send]').disabled=true;
  try{panel.querySelector('[data-assistant-message]').value=sessionStorage.getItem(assistantMessageKey(panel))||'';}catch(_){}
  void assistantRefresh(panel);
}
function assistantFill(panel,value) {
  const put=(selector,text)=>{const el=$(selector);if(!el)throw new Error('编辑区已关闭，请重新打开');el.value=text;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};
  const kind=panel.dataset.assistantKind;
  if(kind==='post'){put('#edit-title',value.title);put('#edit-body',value.body);void preview();}
  else if(kind==='outline'){put('#workflow-title',value.title);put('#workflow-outline',value.body);}
  else if(kind==='body'){put('#workflow-result-title',value.title);put('#workflow-result-body',value.body);}
  else if(kind==='variant'){
    put('#variant-title',value.title);put('#variant-summary',value.summary);put('#variant-tags',value.tags.join(', '));
    if($('#variant-body'))put('#variant-body',value.body);
    if($('#variant-caption'))put('#variant-caption',value.caption);
    value.pages.forEach((p,i)=>{put('#variant-page-title-'+i,p.title);put('#variant-page-text-'+i,p.text);put('#variant-page-visual-'+i,p.visual);});
  }else if(kind==='plan'){
    if(value.pages.length!==illustrationPlan.pages.length)throw new Error('AI建议改变了页数，请先在计划中调整页数，再继续对话。');
    value.pages.forEach((p,i)=>{for(const field of ['title','slot','caption','aspect','text','prompt'])put('#illustration-'+field+'-'+i,p[field]||'');put('#illustration-position-'+i,p.placement||'');});
  }
}
document.addEventListener('input',e=>{
  const panel=e.target.closest('.editor-assistant');
  if(panel&&e.target.matches('[data-assistant-message]')){
    try{sessionStorage.setItem(assistantMessageKey(panel),e.target.value);}catch(_){}
    return;
  }
  assistantLivePanels.forEach(p=>{
    if(e.target!==assistantTextArea(p.dataset.assistantKind))return;
    const info=assistantPanels.get(p);info.candidate=null;assistantComposeState(p);
  });
});
document.addEventListener('select',e=>{
  document.querySelectorAll('.editor-assistant').forEach(panel=>{
    const info=assistantPanels.get(panel);if(!info||e.target!==assistantTextArea(panel.dataset.assistantKind))return;
    const start=e.target.selectionStart,end=e.target.selectionEnd;if(start===end)return;
    // Python indices count code points; browser selections count UTF-16 units.
    info.candidate={start:[...e.target.value.slice(0,start)].length,end:[...e.target.value.slice(0,end)].length,text:e.target.value.slice(start,end),body:e.target.value,stale:false};
    // Selecting text may just mean copying a link. Scope changes only on an explicit click.
    assistantComposeState(panel);
  });
},true);
document.addEventListener('keydown',e=>{
  if(!e.target.matches('[data-assistant-message]')||e.isComposing||!e.ctrlKey&&!e.metaKey||e.key!=='Enter')return;
  e.preventDefault();e.target.closest('.editor-assistant').querySelector('[data-assistant-send]').click();
});
document.addEventListener('click',async e=>{
  const imageButton=e.target.closest('[data-assistant-image]');
  if(imageButton){
    const panel=$('#illustration-assistant');if(!panel)return;
    assistantImageTargets.set(illustrationPlan.id,imageButton.dataset.assistantImage);
    assistantSwitch(panel,'image',imageButton.dataset.assistantImage);panel.scrollIntoView({behavior:'smooth',block:'nearest'});return;
  }
  const button=e.target.closest('[data-assistant-action]');if(!button||button.disabled)return;
  const panel=button.closest('.editor-assistant'),info=assistantPanels.get(panel);if(!info)return;
  const action=button.dataset.assistantAction;
  try{
    if(action==='range-whole'||action==='dismiss-selection'){
      info.selection=null;info.candidate=null;panel.querySelector('[data-assistant-range]').value='whole';
      assistantComposeState(panel);assistantStatus(panel,'已切回当前全文，输入内容已保留。');return;
    }
    if(action==='range-selection'){
      if(!info.candidate||info.candidate.body!==assistantTextArea(panel.dataset.assistantKind)?.value)throw new Error('请在当前编辑区重新选择片段，输入内容会保留');
      info.selection={...info.candidate};info.candidate=null;panel.querySelector('[data-assistant-range]').value='selection';
      assistantComposeState(panel);assistantStatus(panel,'仅修改已确认的选中片段，其余内容保留。');panel.querySelector('[data-assistant-message]').focus({preventScroll:true});return;
    }
    if(action==='clear-reference'){info.reference=null;assistantComposeState(panel);return;}
    if(action==='refine'){
      const turn=info.data?.turns.find(t=>t.id===button.dataset.id);if(!turn)return;
      info.reference=turn.id;info.selection=null;info.candidate=null;panel.querySelector('[data-assistant-range]').value='whole';
      const input=panel.querySelector('[data-assistant-message]');
      if(!input.value.trim())input.value=turn.source_changed?turn.message:'在上一版建议的基础上，';
      input.dispatchEvent(new Event('input',{bubbles:true}));assistantComposeState(panel);input.focus({preventScroll:true});
      assistantStatus(panel,'可以继续描述调整要求；会结合当前编辑区和这版建议生成新稿。');return;
    }
    if(action==='undo'){
      if(!info.undo)return;
      if(!assistantEqual(assistantDraft(panel),info.undo.after))throw new Error('应用后你又编辑了内容，已保留当前稿；请在编辑区手动撤销');
      const before=info.undo.before;info.undo=null;assistantFill(panel,before);assistantComposeState(panel);
      assistantStatus(panel,'已撤销上次应用，编辑区恢复到应用前。');return;
    }
    if(action==='refresh'){await assistantRefresh(panel);return;}
    if(action==='read'){
      const turn=info.data?.turns.find(t=>t.id===button.dataset.id);if(!turn?.result?.replacement)return;
      const dialog=document.createElement('dialog');dialog.className='assistant-reader';
      dialog.innerHTML=`<header><h2>修改建议 · ${esc(assistantNames[panel.dataset.assistantKind])}</h2><button class="button" data-close-reader>返回对话</button></header><div class="assistant-reader-body"><p>${esc(turn.result.reply)}</p>${assistantCompare(turn)}</div>`;
      document.body.append(dialog);dialog.querySelector('[data-close-reader]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();dialog.showModal();return;
    }
    if(action==='back'){assistantImageTargets.delete(info.baseId);assistantSwitch(panel,info.baseKind,info.baseId);return;}
    if(action==='continue-image'){assistantImageTargets.set(info.baseId,button.dataset.id);assistantSwitch(panel,'image',button.dataset.id);return;}
    if(action==='adopt-image'){
      illustrationPlan=await api('/illustrations/'+button.dataset.plan);
      await handleIllustrationAction({dataset:{action:'illustration-adopt',id:button.dataset.id}});return;
    }
    if(action==='retry'){button.disabled=true;const data=await api('/editor-assistant/turns/'+button.dataset.id+'/retry',{});assistantRender(panel,data);return;}
    if(!info.data)throw new Error('请等待当前工作项读取完成');
    if(action==='send'){
      if(info.sending)return;
      const requestedTarget=panel.dataset.assistantKind+'/'+panel.dataset.assistantId;
      info.sending=true;button.disabled=true;
      await assistantRefresh(panel);
      if(!panel.isConnected)return;
      if(requestedTarget!==panel.dataset.assistantKind+'/'+panel.dataset.assistantId){info.sending=false;if(info.data)assistantRender(panel,info.data);return;}
      const input=panel.querySelector('[data-assistant-message]'),message=input.value.trim();if(!message)throw new Error('请填写修改要求或问题');
      const draft=assistantDraft(panel),kind=panel.dataset.assistantKind,id=panel.dataset.assistantId;
      let selection=null;
      if(panel.querySelector('[data-assistant-range]').value==='selection'&&assistantTextArea(kind)){
        if(!assistantSelection(panel)){assistantComposeState(panel);throw new Error('选中片段已改动：可重新选段，或点击“改为整篇修改”。你的输入已保留。');}
        selection={start:info.selection.start,end:info.selection.end};
      }
      info.sending=true;button.disabled=true;assistantStatus(panel,'正在提交修改要求…');
      // Keep the same id after an uncertain transport outcome; never duplicate a billed request.
      const values={revision:info.data.revision,draft,selection,message,mode:kind==='image'?'edit':panel.querySelector('[data-assistant-mode]').value,...(info.reference?{reference_turn_id:info.reference}:{})};
      const key=JSON.stringify(values);if(info.pendingKey!==key){info.pendingKey=key;info.requestId=crypto.randomUUID().replaceAll('-','');}
      const data=await api('/editor-assistant/'+kind+'/'+id,{...values,request_id:info.requestId});
      info.sending=false;
      if(panel.dataset.assistantId!==id||panel.dataset.assistantKind!==kind)return;
      if(input.value.trim()===message){input.value='';try{sessionStorage.removeItem(assistantMessageKey(panel));}catch(_){}}
      info.pendingKey=null;info.reference=null;info.selection=null;info.candidate=null;info.notice=null;panel.querySelector('[data-assistant-range]').value='whole';assistantRender(panel,data);return;
    }
    if(action==='accept'){
      const turn=info.data.turns.find(t=>t.id===button.dataset.id),draft=assistantDraft(panel);
      if(!turn||!assistantEqual(draft,turn.draft))throw new Error('编辑区已变化，旧建议继续保留；请基于当前内容继续对话');
      if(panel.dataset.assistantKind==='variant'&&turn.result.replacement.pages.length!==repurposeCurrent.content.pages.length)throw new Error('建议改变了图集页数，请新建改编任务处理；当前编辑保留');
      if(panel.dataset.assistantKind==='plan'&&turn.result.replacement.pages.length!==illustrationPlan.pages.length)throw new Error('建议改变了页数，请先在配图计划调整页数再继续');
      button.disabled=true;
      const result=await api('/editor-assistant/turns/'+turn.id+'/accept',{draft:turn.draft});
      if(!panel.isConnected||!assistantEqual(assistantDraft(panel),draft))throw new Error('等待期间编辑区已变化，建议保留，未覆盖当前内容');
      info.undo={before:draft,after:result.replacement};assistantFill(panel,result.replacement);info.selection=null;info.candidate=null;
      panel.querySelector('[data-assistant-range]').value='whole';
      await assistantRefresh(panel);assistantStatus(panel,'已更新编辑区，可继续对话或撤销；确认后保存。');
    }
  }catch(err){assistantStatus(panel,err.message,true);info.sending=false;if(button.isConnected)button.disabled=false;}
});
document.addEventListener('DOMContentLoaded',()=>{
  const mount=()=>{
    assistantLivePanels.forEach(panel=>{if(!panel.isConnected){const info=assistantPanels.get(panel);clearTimeout(info?.timer);info?.resize?.disconnect();assistantLivePanels.delete(panel);}});
    document.querySelectorAll('.editor-assistant').forEach(assistantMount);
  };
  new MutationObserver(mount).observe(document.querySelector('.workspace'),{childList:true,subtree:true});
  new MutationObserver(mount).observe(document.querySelector('#modal-content'),{childList:true,subtree:true});mount();
});
let assistantFitFrame;
const assistantFitAll=()=>{cancelAnimationFrame(assistantFitFrame);assistantFitFrame=requestAnimationFrame(()=>assistantLivePanels.forEach(assistantFit));};
window.addEventListener('resize',assistantFitAll);
window.addEventListener('scroll',assistantFitAll,{passive:true});
document.addEventListener('toggle',e=>{if(e.target.matches?.('details.editor-context'))assistantFitAll();},true);
