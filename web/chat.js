let chatSequence = 0, chatTimer, chatLoadedId = '', chatRendering = '', chatTaskSignature = '';
const chatDrafts = new Map(), chatRequests = new Map(), chatSending = new Set(), chatActionRequests = new Map();
let currentChat = null, chatProfileTouched = false, chatHistoryFilter = 'active', chatPlatformView = '';
const chatTaskBusy = new Set();
const chatStatus = {queued:'等待处理',running:'正在处理',completed:'已完成',failed:'失败',interrupted:'已中断',uncertain:'结果待核对',outline_review:'等待确认提纲',review:'待审阅',applied:'已采用',unavailable:'暂不可读取'};
const chatId = () => crypto.randomUUID().replaceAll('-','');

function chatTurnLabel(turn) {
  if(turn.status!=='completed')return chatStatus[turn.status]||turn.status;
  if(!turn.steps.length)return '已回复 · 未执行操作';
  return turn.steps.some(s=>s.status==='completed'&&s.result.tasks?.length&&s.name!=='task_status')?'创作任务已安排':'本轮操作结束';
}

function chatRetrievalHtml(turn) {
  const r=turn.retrieval;if(!r)return '';
  const label={not_searched:'本轮尚未检索共享资料',unavailable:'共享资料检索未完成',no_match:'已收录范围未匹配；不代表本机没有资料',matched:'已查询共享资料'}[r.status]||'';
  return `<p class="tip">${esc(label)}${r.status==='matched'?` · 已读 ${r.read_count} 份 · 已取材 ${r.imported_count} 份`:''}</p>${['no_match','unavailable'].includes(r.status)?`<p class="tip">${esc(r.scope)}。<a href="#library">检查共享资料来源</a></p>`:''}`;
}

function chatReceipt(step) {
  const r=step.result, posts=r.posts || (r.post?[r.post]:[]);
  return `<details class="chat-receipt"><summary>${esc(step.label)} · ${esc(chatStatus[step.status] || step.status)}${r.reused?' · 复用已取材笔记':''}</summary>
    ${r.error?`<p class="error-inline">${esc(r.error)}</p>`:''}
    ${r.scope?`<p class="tip">${esc(r.scope)}</p>`:''}${r.hint?`<p class="tip">${esc(r.hint)}</p>`:''}
    ${r.items?`<p>${r.catalogue_fallback?'已收录范围未匹配，以下为目录线索':'找到 '+esc(r.matched)+' 份'}；本次列出 ${r.items.length} 份。</p><ul>${r.items.map(i=>`<li>${esc(i.name)} · v${i.version}${i.kind==='image'?' · 原始图片，尚未预览':''}<small>${esc(i.source)} / ${esc(i.relative_path)}</small></li>`).join('')}</ul><a href="#library">打开共享资料库预览或检查来源更新</a>`:''}
    ${posts.map(p=>`<p><a href="#edit/${esc(p.id)}">${esc(p.title)}</a> · v${esc(p.version)}</p>`).join('')}
    ${r.profile_update?`<p>已更新画像：${esc(r.profile_update.field)} → ${esc(r.profile_update.value)} · v${r.profile_update.version}</p><a href="#profile/${esc(r.profile_update.id)}">查看画像设定与历史</a>`:''}${r.profiles?`<ul>${r.profiles.map(p=>`<li>${esc(p.name)}</li>`).join('')}</ul>`:''}
    ${r.body?`<pre class="chat-preview">${esc(r.body)}</pre>${r.truncated?'<p>仅展示前半部分，完整稿件请打开文章。</p>':''}`:''}
    ${r.image_references?.length?`<p>笔记包含 ${r.image_references.length} 处图片引用，尚未查看画面；请在原资料中核对。</p>`:''}${r.evidence_boundary?`<p class="tip">${esc(r.evidence_boundary)}</p>`:''}
    ${r.memory?`<p>候选经验：${esc(r.memory.content)}</p><button class="button" data-action="profile-memory-open" data-id="${esc(r.memory.profile_id)}">核对并确认经验</button>`:''}
    ${r.tasks?'<p>进度与结果见下方任务卡。</p>':''}</details>`;
}

function platformResult(t) {
  const v=t.visuals||{status:'not_started',pages:[]}, c=t.content||{}, ready=v.status==='ready';
  const status=t.status==='applied'?'已采用':['failed','interrupted'].includes(t.status)?'文字生成需要处理':t.status!=='review'?'正在改编文字':ready?'图文已齐 · 待审阅':v.status==='failed'?'图片需要处理':'文字已完成 · 图片准备中';
  const label=platformNames()[t.platform]||t.platform;
  return `<article class="chat-task platform-result" data-chat-task="${esc(t.id)}" data-chat-kind="repurpose" data-platform-result="${esc(t.platform)}">
    <header class="platform-result-head"><div><span class="platform-name">${esc(label)}</span><p class="muted">${t.platform==='xiaohongshu'?'竖版图集 + 配文 + 话题':'文章 + 封面 + 正文配图'}</p></div><span class="tag">${esc(status)}</span></header>
    <h2>${esc(c.title||'正在生成平台标题…')}</h2>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(t.model_provenance):''}
    ${t.error||t.status==='interrupted'?`<p class="error-inline">${esc(t.error)}</p><button class="button" data-chat-action="retry_task">重试文字生成</button>`:''}
    <section class="platform-images"><div class="actions"><h3>${t.platform==='xiaohongshu'?'图集预览':'封面与配图'}</h3><small>${v.mode==='ai'?'AI 图片':'排版图卡'} · ${v.complete||0}/${v.total||0} 张就绪</small></div>
    <div class="platform-gallery">${(v.pages||[]).map((p,i)=>`<figure>${p.url?`<a href="${esc(p.url)}" target="_blank" rel="noopener"><img src="${esc(p.url)}" alt="${esc(p.title)}" loading="lazy"></a>`:`<div class="image-placeholder">${['failed','interrupted'].includes(p.status)?'图片未完成':p.status==='running'?'正在生成图片…':'等待生成图片'}</div>`}<figcaption>${i+1}. ${esc(p.title)} ${p.url?`<a href="${esc(p.url)}" download>下载</a>`:''}</figcaption>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(p.model_provenance):''}${p.error?`<p class="error-inline">${esc(p.error)}</p>`:''}</figure>`).join('')||'<p class="muted">文字完成后自动准备图片。</p>'}</div>
    ${t.status==='review'&&['not_started','failed'].includes(v.status)?`<button class="button" data-chat-action="prepare_deliverable">${v.status==='not_started'?'生成完整图文':'重试未完成图片'}</button>`:''}</section>
    <section class="platform-text"><h3>${t.platform==='xiaohongshu'?'配文与话题':'文章预览'}</h3>${t.platform==='xiaohongshu'?`<div class="platform-caption">${esc(c.caption||'')}</div><p>${(c.tags||[]).map(x=>'#'+esc(x)).join(' ')}</p>`:`<div class="markdown-body">${t.preview_html||'<p>正文生成中…</p>'}</div>`}</section>
    ${t.source_changed||t.profile_changed?'<label><input type="checkbox" class="chat-ack-source" data-chat-edit="ack-source">已核对母稿或画像变化，采用这份旧快照图文</label>':''}
    <div class="actions platform-result-actions">${t.status==='review'?`<button class="button primary" data-chat-action="adopt_deliverable" ${ready?'':'disabled'}>采用并发表</button><button class="button" data-action="repurpose-show" data-id="${esc(t.id)}">编辑文案</button><button class="button" data-chat-copy="${esc(t.id)}">复制${t.platform==='xiaohongshu'?'配文':'正文'}</button>`:''}${t.applied_post_id?`<a class="button primary" href="#edit/${esc(t.applied_post_id)}">打开已采用图文</a>`:''}</div>
    <p class="notice">${t.distribution?esc(t.distribution.message||t.distribution.status):'审阅采用后进入公开发表检查，连接未就绪时保留稿件并提示处理。'}</p><p class="muted">${t.status==='applied'?'已保存为独立平台稿。':'审阅文字和图片后，一次采用整份图文。'}送稿状态请在发布队列核对。</p><p data-chat-card-error role="status" class="error-inline"></p></article>`;
}
function selectPlatformResults() {
  const cards=[...document.querySelectorAll('[data-platform-result]')];
  if(!cards.some(c=>c.dataset.platformResult===chatPlatformView))chatPlatformView=Object.keys(platformNames()).find(p=>cards.some(c=>c.dataset.platformResult===p))||'';
  cards.forEach(c=>c.hidden=c.dataset.platformResult!==chatPlatformView);
  document.querySelectorAll('[data-platform-view]').forEach(b=>{b.classList.toggle('active',b.dataset.platformView===chatPlatformView);b.setAttribute('aria-pressed',String(b.dataset.platformView===chatPlatformView));});
}

function chatTaskCard(t) {
  if(t.kind==='repurpose')return platformResult(t);
  const title=t.status==='applied'&&t.current_post?.title?t.current_post.title:t.selected_title || t.post_title || t.source_title || t.current_post?.title || ({workflow:'创作任务',topic:'选题任务',review:'审稿任务',repurpose:'平台改编',illustration:'配图规划'}[t.kind]);
  const button=(action,label,extra='')=>`<button class="button" data-chat-action="${action}" ${extra}>${label}</button>`;
  const busy=chatTaskBusy.has(t.id);
  let content='';
  if(t.kind==='topic')content=`${(t.candidates||[]).map((c,i)=>`<section class="panel panel-body"><h3>${i+1}. ${esc(c.title)}</h3><p>${esc(c.reader_value)}</p><p>${esc(c.angle||'')}</p>${!t.selected_post_id?button('select_topic','选这个并生成提纲',`data-index="${i}"`):t.selected_index===i?'<span class="tag">已选择</span>':''}</section>`).join('')}`;
  if(t.kind==='workflow')content=`${t.outline?`<details ${t.status==='outline_review'?'open':''}><summary>提纲</summary>${t.status==='outline_review'?`<label>本篇采用的标题<input class="chat-title" data-chat-edit="title" list="chat-titles-${esc(t.id)}" maxlength="150" value="${esc(t.selected_title||t.post_title||'')}"><datalist id="chat-titles-${esc(t.id)}">${(t.title_options||[]).map(title=>`<option value="${esc(title)}"></option>`).join('')}</datalist></label>`:''}<textarea aria-label="修改提纲" class="chat-outline" data-chat-edit="outline" rows="8" ${t.status==='outline_review'?'':'readonly'}>${esc(t.outline)}</textarea>${t.status==='outline_review'?button('confirm_outline','确认标题、提纲并生成正文',t.input_changes?.length?'disabled data-chat-blocked="true"':''):''}</details>`:''}
    ${t.body?`<details ${t.status==='review'?'open':''}><summary>${t.status==='applied'?'已采用的生成记录':'正文待审稿'}</summary><p>采用标题：${esc(t.selected_title||t.post_title||'')}</p><pre class="chat-preview">${esc(t.body)}</pre>${t.status==='review'?`<ul>${(t.review?.warnings||[]).map(w=>`<li>${esc(w)}</li>`).join('')}</ul>${t.source_changed?'<p class="notice">原文章已修改，此结果保留供比较。请打开任务基于最新稿件继续，避免覆盖。</p>':''}${button('adopt_body','采用标题与正文',t.source_changed?'disabled data-chat-blocked="true"':'')}`:''}</details>`:''}`;
  if(t.kind==='review')content=`${t.source_changed&&t.status!=='applied'?`<div class="notice">正文已从 v${esc(t.source_version)} 更新到 v${esc(t.current_post?.version)}，旧建议只供对照。</div>${button('start_review','对最新保存稿重新审稿')}`:''}<p>${esc(t.result?.summary||'等待审稿结果')}</p>${(t.result?.issues||[]).map(i=>`<details><summary>${esc(i.reason)}</summary><p>原句：${esc(i.quote)}</p><p>建议：${esc(i.replacement)}</p>${i.applicable&&t.status==='review'&&!t.source_changed?`<label><input type="checkbox" data-chat-edit="issue-${esc(i.id)}" data-chat-issue="${esc(i.id)}">采用此建议</label>${i.needs_verification?`<label><input type="checkbox" data-chat-edit="verified-${esc(i.id)}" data-chat-verified="${esc(i.id)}">我已核实此项事实修改</label>`:''}`:''}</details>`).join('')}
    ${t.profile_changed&&!t.source_changed?'<label><input type="checkbox" data-chat-edit="ack-profile" class="chat-ack-profile">已核对旧画像或记忆，仍采用这次建议</label>':''}${t.status==='review'&&!t.source_changed&&(t.result?.issues||[]).some(i=>i.applicable)?button('apply_review','采用勾选的建议'):''}`;
  if(t.kind==='repurpose')content=`<p>${esc(t.content?.title||'')}</p>${t.status==='review'?`<button class="button" data-action="repurpose-show" data-id="${esc(t.id)}">审阅、编辑并采用平台稿</button>`:''}`;
  if(t.kind==='illustration')content=`${['failed','interrupted'].includes(t.status)?(typeof modelProvenanceHtml==='function'?modelProvenanceHtml(t.next_models?.illustration_plan):'')+button('retry_plan','重试配图规划'):''}${t.source_changed||t.profile_changed||(t.candidates||[]).some(c=>c.plan_changed)?'<label><input type="checkbox" data-chat-edit="ack-image" class="chat-ack-image">已核对文章、画像或计划变化，仍采用旧快照图片</label>':''}${t.status==='review'?(t.pages||[]).map((p,i)=>`<details><summary>第${i+1}页 · ${esc(p.title)}</summary><p>${esc(p.prompt)}</p>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(t.next_models?.[p.slot==='cover'?'cover':'illustration']):''}${button('generate_image','生成本页候选图',`data-page-index="${i}"`)}</details>`).join(''):''}
    ${(t.candidates||[]).map(c=>`<section><p>第${c.page_index+1}页 · ${esc(chatStatus[c.status]||c.status)}</p>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(c.model_provenance):''}${c.error?`<p class="error-inline">${esc(c.error)}</p>`:''}${c.image_url?`<img src="${esc(c.image_url)}" alt="待采用的候选图" style="max-width:100%;max-height:260px">${t.adopted?.some(a=>a.candidate_id===c.id)?'<span class="tag">已采用</span>':button('adopt_image','采用此图',`data-candidate-id="${esc(c.id)}"`)}`:''}</section>`).join('')}`;
  let next='';
  if(t.status==='applied'&&['workflow','review','repurpose'].includes(t.kind))next=`<div class="chat-next"><p>接下来</p>${button('start_review','继续审稿')}${button('plan_images','安排配图规划')}
    ${t.kind!=='repurpose'&&t.current_post?.id?`<a class="button primary" href="#edit/${esc(t.current_post.id)}">下一步：编辑图文与准备草稿</a><a class="button" href="#queue">查看送稿进度</a><p>已采用到文章 v${esc(t.current_post.version)}。在图文编辑器补齐摘要、封面和章节配图，再准备公众号草稿。送入草稿箱不等于公开发表。</p>`:''}
    ${t.kind!=='repurpose'?`<details><summary>改编到其他平台</summary>${repurposeTargets().map(({id,name})=>`<label class="check-label"><input type="checkbox" data-chat-edit="platform-${id}" data-chat-platform="${id}" checked>${esc(name)}</label>`).join('')}${button('start_repurpose','生成所选平台待审稿')}</details>`:''}</div>`;
  return `<article class="chat-task" data-chat-task="${esc(t.id)}" data-chat-kind="${esc(t.kind)}"><div class="actions"><strong>${esc(title)}</strong><span class="tag">${esc(chatStatus[t.status]||t.status||'待处理')}</span><a class="button small" href="${esc(t.href)}">打开任务 →</a></div>
    <div class="chat-card-content">${t.next_step?`<p class="notice blue">${esc(t.next_step)}</p>`:''}${typeof modelProvenanceHtml==='function'?(t.model_stages||[t.model_provenance]).map(modelProvenanceHtml).join(''):''}${t.error?`<p class="error-inline">${esc(t.error)}</p>`:''}${['failed','interrupted','canceled'].includes(t.status)&&t.kind!=='illustration'?button('retry_task','按原素材重试'):''}${content}${next}
    ${t.status!=='applied'&&t.input_changes?.length?`<p class="error-inline">输入有变化：${esc(t.input_changes.join('、'))}。请打开任务调整后再确认。</p>`:''}
    ${t.kind==='workflow'&&t.status==='outline_review'?'<div data-chat-planned="body"></div>':''}
    <p data-chat-card-error role="status" class="error-inline">${busy?'正在保存操作…':''}</p></div></article>`;
}

async function drawChat() {
  const route=location.hash, cid=route.split('/')[1]||'', sequence=++chatSequence;
  clearTimeout(chatTimer);
  const valid=()=>sequence===chatSequence && location.hash===route;
  const samePage=()=>location.hash===route && chatLoadedId===cid && $('#chat-prompt');
  if(!$('#chat-prompt') || chatLoadedId!==cid) {
    chatLoadedId=cid;chatRendering='';chatTaskSignature='';chatProfileTouched=false;currentChat=null;
    $('#main').innerHTML=heading('对话安排','说出你想做的事，纸飞机查素材、安排任务，并在这里展示结果。',directoryButton() + '<a class="button" href="#chat">＋ 新的对话</a>')+
      `<div class="chat-layout"><aside class="chat-history"><h2>对话历史</h2><label>显示<select id="chat-history-filter"><option value="active">最近对话</option><option value="archived">已归档</option><option value="deleted">回收站</option></select></label><nav id="chat-history" aria-label="对话记录"></nav></aside><section class="chat-panel">
      <div id="chat-tasks"></div><details id="chat-log" open><summary>对话与执行记录</summary><div id="chat-messages" role="log" aria-live="polite"></div></details>
      <form id="chat-form"><label id="chat-profile-label">本次画像<select id="chat-profile">${!state.profiles.length?'<option value="">暂无画像，请先到画像管理创建</option>':''}${state.profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></label>
      <label for="chat-prompt">给纸飞机安排任务</label><textarea id="chat-prompt" rows="3" maxlength="12000" placeholder="用共享素材库继续写 07，关于智能体记忆，先给我提纲。"></textarea>
      <div class="actions"><button class="button primary" id="chat-send">发送安排</button><span id="chat-status" role="status"></span></div></form>
      <div id="chat-planned-model"></div>
      <p class="muted">提纲确认、正文采用仍由你决定。已有任务会继续显示进度；对话使用设置中的文字模型。</p></section></div>`;
    $('#chat-prompt').value=chatDrafts.get(cid)||'';
    if(typeof renderPlannedModel==='function')void renderPlannedModel($('#chat-planned-model'),'chat');
    $('#chat-history-filter').value=chatHistoryFilter;
    $('#chat-history-filter').onchange=()=>{chatHistoryFilter=$('#chat-history-filter').value;void drawChat();};
    $('#chat-history').onclick=async e=>{
      const b=e.target.closest('[data-chat-organize]');if(!b)return;
      const action=b.dataset.chatOrganize,id=b.dataset.id;
      if(action==='delete'&&!confirm('将这段对话移入回收站？文章、素材和已创建任务会保留，可在回收站恢复对话。'))return;
      if(action==='purge'&&!confirm('彻底删除这段对话？对话消息将无法恢复，已生成的文章、素材和任务仍会保留。'))return;
      b.disabled=true;
      try{await api('/chat/'+id+'/organize',{action});if(id===cid&&action!=='restore'){location.hash='#chat';}await drawChat();}
      catch(error){toast(error.message);if(b.isConnected)b.disabled=false;}
    };
    $('#chat-profile').onchange=async()=>{
      chatProfileTouched=true;
      if(cid){try{await api('/chat/'+cid+'/profile',{profile_id:$('#chat-profile').value});await drawChat();}catch(e){toast(e.message);if(currentChat)$('#chat-profile').value=currentChat.profile_id||'';}}
    };
    $('#chat-prompt').oninput=()=>chatDrafts.set(cid,$('#chat-prompt').value);
    $('#chat-form').onsubmit=async e=>{
      e.preventDefault();const message=$('#chat-prompt').value.trim();if(!message)return;
      const button=$('#chat-send'),key=cid||'new';if(chatSending.has(key))return;
      button.disabled=true;chatSending.add(key);let request=chatRequests.get(key);
      if(!request || request.message!==message){request={id:chatId(),message,conversationId:cid,createId:chatId(),profileId:$('#chat-profile').value};chatRequests.set(key,request);}
      try {
        if(!request.conversationId) {
          const created=await api('/chat',{request_id:request.createId,title:message.slice(0,65),profile_id:request.profileId});
          request.conversationId=created.id;
        }
        await api('/chat/'+request.conversationId+'/messages',{request_id:request.id,message});
        chatRequests.delete(key);chatDrafts.delete(cid);
        if(samePage()){$('#chat-prompt').value='';location.hash='#chat/'+request.conversationId;await drawChat();}
      }catch(error){if(samePage())$('#chat-status').textContent=error.message;}
      finally{chatSending.delete(key);if(button.isConnected)button.disabled=false;}
    };
    $('#chat-tasks').onclick=async e=>{
      const tab=e.target.closest('[data-platform-view]');if(tab){chatPlatformView=tab.dataset.platformView;selectPlatformResults();return;}
      const copy=e.target.closest('[data-chat-copy]');if(copy){const item=currentChat?.tasks.find(t=>t.id===copy.dataset.chatCopy);if(item){try{await navigator.clipboard.writeText(item.platform==='xiaohongshu'?(item.content.caption||''):(item.content.body||''));toast('已复制');}catch{toast('复制失败，请选择正文复制');}}return;}
      const b=e.target.closest('[data-chat-action]');if(!b||b.disabled)return;
      const card=b.closest('[data-chat-task]'),t=currentChat?.tasks.find(t=>t.id===card.dataset.chatTask);
      if(!t||chatTaskBusy.has(t.id))return;
      const action=b.dataset.chatAction, values={action,id:t.id,kind:t.kind};
      if(action==='confirm_outline'){values.outline=card.querySelector('.chat-outline').value;values.outline_version=t.outline_version;values.selected_title=card.querySelector('.chat-title').value;}
      if(action==='select_topic')values.index=Number(b.dataset.index);
      if(['adopt_body','apply_review','start_review','start_repurpose','plan_images','adopt_image'].includes(action))values.post_version=action==='adopt_body'?t.post_version:t.current_post?.version;
      if(action==='start_repurpose')values.platforms=[...card.querySelectorAll('[data-chat-platform]:checked')].map(el=>el.dataset.chatPlatform);
      if(action==='apply_review'){
        values.issues=[...card.querySelectorAll('[data-chat-issue]:checked')].map(el=>({id:el.dataset.chatIssue,verified:Boolean(card.querySelector(`[data-chat-verified="${el.dataset.chatIssue}"]`)?.checked)}));
        values.ack_profile=Boolean(card.querySelector('.chat-ack-profile')?.checked);
      }
      if(action==='generate_image'){values.page_index=Number(b.dataset.pageIndex);values.version=t.version;}
      if(action==='adopt_image'){values.candidate_id=b.dataset.candidateId;values.ack_changes=Boolean(card.querySelector('.chat-ack-image')?.checked);}
      if(action==='retry_plan')values.version=t.version;
      if(action==='prepare_deliverable')values.mode=t.visuals?.mode||'ai';
      if(action==='adopt_deliverable'){values.publish_public=true;values.version=t.version;values.ack_source_changes=Boolean(card.querySelector('.chat-ack-source')?.checked);}
      const key=JSON.stringify(values);
      if(!chatActionRequests.has(key))chatActionRequests.set(key,chatId());
      values.request_id=chatActionRequests.get(key);
      b.disabled=true;chatTaskBusy.add(t.id);
      try{
        await api('/chat/'+cid+'/action',values);chatActionRequests.delete(key);await refresh(false);
        if(samePage())await drawChat();
      }catch(error){if(card.isConnected)card.querySelector('[data-chat-card-error]').textContent=error.message;else toast(error.message);}
      finally{chatTaskBusy.delete(t.id);if(samePage()){const live=document.querySelector(`[data-chat-task="${t.id}"]`);live?.querySelectorAll('[data-chat-action]').forEach(el=>el.disabled=el.dataset.chatBlocked==='true'||(el.dataset.chatAction==='adopt_deliverable'&&currentChat?.tasks.find(x=>x.id===t.id)?.visuals?.status!=='ready'));const status=live?.querySelector('[data-chat-card-error]');if(status?.textContent==='正在保存操作…')status.textContent='';}if(b.isConnected)b.disabled=b.dataset.chatBlocked==='true';}
    };
  }
  try {
    const [list,chat]=await Promise.all([api('/chat?visibility='+chatHistoryFilter),cid?api('/chat/'+cid):Promise.resolve(null)]);
    if(!valid())return;
    $('#chat-history').innerHTML=list.conversations.map(c=>`<div class="chat-history-item"><a class="${c.id===cid?'active':''}" href="#chat/${esc(c.id)}">${esc(c.title)}</a><div class="actions">${c.visibility==='active'?`<button class="button small" data-chat-organize="archive" data-id="${c.id}">归档</button>`:`<button class="button small" data-chat-organize="restore" data-id="${c.id}">恢复</button>`}${c.visibility!=='deleted'?`<button class="button small" data-chat-organize="delete" data-id="${c.id}">删除</button>`:`<button class="button small danger" data-chat-organize="purge" data-id="${c.id}">彻底删除</button>`}</div></div>`).join('')||'<p class="muted">这里暂无对话。</p>';
    currentChat=chat;
    if(!chatProfileTouched||cid)$('#chat-profile').value=chat?.profile_id||list.default_profile_id||state.profiles[0]?.id||'';
    $('#chat-profile').disabled=Boolean(chat?.turns.some(t=>['queued','running'].includes(t.status)));
    $('#chat-profile-label').title=cid?'此画像用于后续对话；已经创建的任务保留原画像':'';
    const turns=chat?.turns||[],signature=JSON.stringify(turns);
    if(signature!==chatRendering) {
      chatRendering=signature;
      $('#chat-messages').innerHTML=turns.length?turns.map(t=>`<div class="chat-message user"><small>我</small><p>${esc(t.message)}</p></div><div class="chat-message assistant"><small>纸飞机 · ${esc(chatTurnLabel(t))}</small>${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(t.model_provenance):''}${chatRetrievalHtml(t)}${memorySnapshotHtml(t.profile_snapshot)}${chatLearningHtml(t.learning)}${t.steps.map(chatReceipt).join('')}${t.reply?`<p>${esc(t.reply)}</p>`:''}${t.error?`<p class="error-inline">${esc(t.error)}</p>`:''}${['failed','interrupted'].includes(t.status)?`<button class="button" data-chat-resume="${esc(t.id)}">核对已有结果并继续</button>`:''}</div>`).join(''):
        `<div class="chat-welcome"><h2>把任务直接交给纸飞机</h2><p>可以接着讨论、修改要求，任务与素材记录会跟着这段对话保存。</p>${['找智能体记忆相关的素材','继续写 07，先生成提纲','给这篇文章安排审稿'].map(text=>`<button class="button" type="button" data-chat-example="${esc(text)}">${esc(text)}</button>`).join('')}</div>`;
      $$('[data-chat-resume]').forEach(b=>b.onclick=()=>{const turn=turns.find(t=>t.id===b.dataset.chatResume);$('#chat-prompt').value='继续之前未完成的安排：'+turn.message+'。先核对已有任务和操作记录，仅完成剩余步骤，不要重复创建。';$('#chat-form').requestSubmit();});
      $$('[data-chat-example]').forEach(b=>b.onclick=()=>{$('#chat-prompt').value=b.dataset.chatExample;chatDrafts.set(cid,b.dataset.chatExample);$('#chat-prompt').focus();});
    }
    const tasksSignature=JSON.stringify(chat?.tasks||[]);
    if(tasksSignature!==chatTaskSignature) {
      // Keep unsaved outline edits when other task cards change during polling.
      const edits=new Map([...document.querySelectorAll('[data-chat-edit]')].map(el=>[el.closest('[data-chat-task]').dataset.chatTask+':'+el.dataset.chatEdit,{value:el.value,checked:el.checked}]));
      chatTaskSignature=tasksSignature;
      const platformTasks=(chat?.tasks||[]).filter(t=>t.kind==='repurpose').sort((a,b)=>Object.keys(platformNames()).indexOf(a.platform)-Object.keys(platformNames()).indexOf(b.platform));
      const tabs=platformTasks.length?`<section class="platform-overview"><h2>各平台图文</h2><p>选择平台，直接审阅文章和图片。</p><nav class="platform-tabs" aria-label="平台选择">${[...new Map(platformTasks.map(t=>[t.platform,t])).values()].map(t=>`<button class="button" data-platform-view="${esc(t.platform)}">${esc(platformNames()[t.platform]||t.platform)}<small>${t.status==='applied'?'已采用':t.visuals?.status==='ready'?'图文就绪':t.visuals?.status==='failed'?'图片待处理':'生成中'}</small></button>`).join('')}</nav></section>`:'';
      $('#chat-tasks').innerHTML=tabs+(chat?.tasks||[]).map(chatTaskCard).join('');
      if(typeof renderPlannedModel==='function')document.querySelectorAll('[data-chat-planned]').forEach(el=>void renderPlannedModel(el,el.dataset.chatPlanned));
      selectPlatformResults();if(platformTasks.length&&!$('#chat-log').dataset.initialized){$('#chat-log').open=false;$('#chat-log').dataset.initialized='true';}
      for(const el of document.querySelectorAll('[data-chat-edit]')){const saved=edits.get(el.closest('[data-chat-task]').dataset.chatTask+':'+el.dataset.chatEdit);if(saved){if(el.type==='checkbox')el.checked=saved.checked;else if(!el.readOnly)el.value=saved.value;}}
      for(const el of document.querySelectorAll('[data-chat-task]'))if(chatTaskBusy.has(el.dataset.chatTask))el.querySelectorAll('[data-chat-action]').forEach(b=>b.disabled=true);
    }
    const busy=turns.some(t=>['queued','running'].includes(t.status))||chatSending.has(cid||'new');$('#chat-send').disabled=busy||Boolean(chat&&chat.visibility!=='active');$('#chat-prompt').disabled=Boolean(chat&&chat.visibility!=='active');
    $('#chat-status').textContent=chat&&chat.visibility!=='active'?'对话已归档或在回收站，请从左侧恢复后继续。':busy?'正在处理，可查看下方操作记录…':'';
    if(cid)chatTimer=setTimeout(()=>{if(valid())void drawChat();},2500);
  }catch(error){if(valid()){$('#chat-status').textContent=error.message+'；正在重新连接，输入已保留。';if(cid)chatTimer=setTimeout(()=>{if(valid())void drawChat();},3000);}}
}
