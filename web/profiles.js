"use strict";

let profileEditor = null, profileDirty = false, newProfileChoice = "", profileReferencesLoading = false;
const profileFields = [
  ["name", "画像名称", 80, "例如：AI 工程实践"],
  ["direction", "定位与内容方向", 2000, "你是谁，有什么独特经历，主要分享什么？"],
  ["audience", "目标读者", 2000, "写给谁看？他们的经验、需求和困惑是什么？"],
  ["goal", "运营目标", 2000, "近期最想达成什么？如同行交流、学习分享、合作咨询，可写优先级。"],
  ["style", "表达风格", 2000, "人称、语气、技术深度、喜欢的结构…"],
  ["preferences", "偏好与红线", 3000, "希望保留什么？哪些内容或表达不能出现？"],
  ["platforms", "运营平台", 1000, "如：公众号、CSDN、掘金、DEV、小红书"],
  ["links", "社媒主页链接", 2000, "每行一个平台与主页链接。当前只保存，不自动抓取。"],
  ["wechat_footer", "公众号文末署名与入口", 2000, "例如：Aiclaw｜更多实践笔记：https://blog.example.com/ 。仅用于此画像的公众号母稿，留空则不追加。"],
  ["memory", "运营经验与反馈", 3000, "记录有依据的读者反馈与复盘，注明来源和时间。"],
  ["visual_style", "视觉风格", 3000, "纸感、笔触、配色、光线、构图与不希望出现的元素，仅作用于这份画像。"],
];

function profileOptions(selected = "") {
  return '<option value="">未选择画像</option>' + (state.profiles || []).map(p =>
    `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(p.name)} · v${p.version}</option>`).join("");
}
function profileName(id) {
  return (state.profiles || []).find(p => p.id === id)?.name || "未选择画像";
}
function profileSummary(profile) {
  if (!profile) return '<p class="tip">本次未选择画像。可在文章信息中选择，或先到“画像管理”创建。</p>';
  return `<div class="notice blue"><strong>账号画像：${esc(profile.name)} · v${profile.version}</strong>
    <p>目标读者：${esc(profile.audience || "未填写")}</p><p>运营目标：${esc(profile.goal || "未填写")}</p>
    ${typeof memorySnapshotHtml === "function" ? memorySnapshotHtml(profile) : ""}
    <details><summary>查看本次画像内容</summary>${profileFields.filter(([k]) => k !== "name").map(([k,label]) =>
      `<p><strong>${label}</strong><br><span class="workflow-preserve">${esc(profile[k] || "未填写")}</span></p>`).join("")}</details></div>`;
}
function drawProfiles() {
  profileEditor = null;
  $("#main").innerHTML = heading("画像管理", "每份画像独立保存定位、读者、运营目标与写作偏好。",
    '<button class="button" data-action="profile-sync-open">云端同步</button><a class="button primary" href="#profile/new">＋ 新建画像</a>') +
    `<div class="cards">${(state.profiles || []).map(p => `<article class="idea-card profile-card">
      <span class="tag blue">v${p.version}</span><span class="tag">创作经验 ${state.profile_learning?.[p.id]?.accepted||0} 条 · 待核对 ${state.profile_learning?.[p.id]?.pending||0} 条</span><h3>${esc(p.name)}</h3><p>${esc(p.direction || "尚未填写内容方向")}</p>
      <p><strong>目标读者：</strong>${esc(p.audience || "未填写")}</p><p><strong>运营目标：</strong>${esc(p.goal || "未填写")}</p>
      <div class="card-footer"><a class="button" href="#profile/${esc(p.id)}">查看与编辑</a>
      <a class="button" href="#profile/${esc(p.id)}/experience">创作经验与学习</a>
      <button class="button" data-action="profile-history-open" data-id="${esc(p.id)}">版本历史</button>
      <button class="button" data-action="profile-duplicate-open" data-id="${esc(p.id)}">复用为新画像</button>
      <a class="button" href="#topics/new/${esc(p.id)}">帮我选题</a>
      <button class="button primary" data-action="profile-create-post" data-id="${esc(p.id)}">用此画像创作</button></div></article>`).join("")}</div>` +
    ((state.profiles || []).length ? "" : empty("创建第一份账号画像", "先填名称即可保存。目标读者与运营目标越明确，选题和写作越有方向。", '<a class="button primary" href="#profile/new">＋ 新建画像</a>'));
}
function drawProfileEditor() {
  const id = location.hash.split("/")[1];
  const tab = location.hash.split('/')[2];
  if(['experience','pending','updates'].includes(tab) && id!=='new') {
    profileEditor=null;profileDirty=false;
    void drawProfileLearning(id,tab).catch(e=>toast(e.message));return;
  }
  if (profileEditor?.id === id && $("#profile-name")) return;
  const existing = (state.profiles || []).find(p => p.id === id);
  if (id !== "new" && !existing) {
    $("#main").innerHTML = empty("画像不存在", "请回到画像管理重新选择。", '<a href="#profiles">返回画像管理</a>');
    return;
  }
  profileEditor = existing ? {...existing} : {id: "new", version: 0};
  profileDirty = false;
  $("#main").innerHTML = heading(id === "new" ? "新建画像" : esc(profileEditor.name),
    "填写你确定的信息；不清楚的可留空，稍后补充。",
    '<button class="button" data-action="profile-back">返回画像列表</button><button class="button primary" data-action="profile-save">保存画像</button>') +
    (id==='new'?'':profileLearningTabs(id))+
    `<section class="panel panel-body spaced"><p id="profile-save-state" role="status" class="tip">${id === "new" ? "尚未保存" : `已保存 · v${profileEditor.version}`}</p>
    ${id === 'new' ? '' : `<div class="actions"><button class="button" data-action="profile-history-open" data-id="${esc(id)}">版本历史</button><button class="button" data-action="profile-duplicate-open" data-id="${esc(id)}">复用为新画像</button></div>`}
    <div class="form-grid">${profileFields.map(([key,label,limit,placeholder]) => `<label>${label}${key === "name" ? " *" : ""}${key === "name" ?
      `<input id="profile-${key}" data-profile-field="${key}" maxlength="${limit}" value="${esc(profileEditor[key])}" placeholder="${placeholder}">` :
      `<textarea id="profile-${key}" data-profile-field="${key}" rows="4" maxlength="${limit}" placeholder="${placeholder}">${esc(profileEditor[key])}</textarea>`}</label>`).join("")}</div>
    <label>真实参考图（最多4张）<select id="profile-visual-references" data-profile-field="visual_references" multiple size="5"></select><small>选择已上传图片作为实际模型输入，支持 images/edits 的服务会收到图片本体。</small></label><div class="actions"><button class="button" data-action="profile-reference-upload">上传参考图</button><button class="button" data-action="profile-aiclaw-style">使用 Aiclaw 温暖工程手绘参考</button></div>
    <p class="tip">文章关联画像后，创作请求会使用该画像。已创建任务保留创建时的版本；这里的修改供之后新建的任务使用。请勿填写登录密码或 API Key。</p></section>`;
  void loadProfileReferences(profileEditor.visual_references || []).catch(e=>toast(e.message));
}
async function loadProfileReferences(selected) {
  const owner=profileEditor, select=$('#profile-visual-references');
  profileReferencesLoading=true;
  try {
    const assets=(await api('/assets')).assets;
    if(profileEditor!==owner||$('#profile-visual-references')!==select)return;
    const choices=assets.filter(a=>(!a.archived||selected.includes(a.url))&&/\.(png|jpg|webp)$/.test(a.url));
    for(const url of selected)if(!choices.some(a=>a.url===url))choices.push({url,title:'原有参考图（请核对）'});
    select.innerHTML=choices.map(a=>`<option value="${esc(a.url)}" ${selected.includes(a.url)?'selected':''}>${esc(a.title)}</option>`).join('');
  } finally {if(profileEditor===owner)profileReferencesLoading=false;}
}
function profileReferenceValues(){return [...$('#profile-visual-references').selectedOptions].map(o=>o.value);}
async function handleProfileAction(action, button) {
  if (action.startsWith('profile-learning-')) return handleProfileLearningAction(action,button);
  if (action.startsWith('profile-history-') || action.startsWith('profile-duplicate-')) return handleProfileVersions(action,button);
  if (action.startsWith('profile-memory-')) return handleMemoryAction(action,button);
  if (action.startsWith('profile-sync-')) return handleProfileSync(action,button);
  if (action === "profile-save") {
    if(profileReferencesLoading)throw new Error('参考图正在加载，请稍后保存');
    const savedId = profileEditor.id;
    const values = Object.fromEntries(profileFields.map(([key]) => [key, $("#profile-" + key).value]));
    values.visual_references=profileReferenceValues();
    const saved = await api("/profiles" + (savedId === "new" ? "" : "/" + savedId), {...values, version: profileEditor.version});
    state.profiles = [...(state.profiles || []).filter(p => p.id !== saved.id), saved];
    // If typing continued during the request, keep the unsaved text and only advance its version.
    profileDirty = profileFields.some(([key]) => $("#profile-" + key).value !== values[key]);
    profileDirty ||= JSON.stringify(profileReferenceValues())!==JSON.stringify(values.visual_references);
    profileEditor = saved;
    if (savedId === "new") {
      history.replaceState(null, "", "#profile/" + saved.id);
      previousHash = location.hash;
    }
    $("#profile-save-state").textContent = profileDirty ? "有未保存的修改" : `已保存 · v${saved.version}`;
    if (savedId === 'new' && !profileDirty) { profileEditor=null; drawProfileEditor(); }
    toast("画像已保存");
  } else if (action === 'profile-aiclaw-style') {
    const owner=profileEditor;
    const preset=await api('/illustrations/aiclaw-preset',{});
    if(profileEditor!==owner||!$('#profile-visual_style'))return;
    $('#profile-visual_style').value=preset.visual_style;
    await loadProfileReferences(preset.visual_references);
    if(profileEditor!==owner||!$('#profile-save-state'))return;
    profileDirty=true;$('#profile-save-state').textContent='风格参考已填入，尚未保存；只作用于此画像';
  } else if (action === 'profile-reference-upload') {
    const owner=profileEditor;
    const fileInput=document.createElement('input');fileInput.type='file';fileInput.accept='image/png,image/jpeg,image/webp';
    fileInput.onchange=async()=>{try{
      const file=fileInput.files[0];if(!file)return;if(file.size>5000000)throw new Error('参考图需在5 MB以内');
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
      if(profileEditor!==owner||!$('#profile-visual-references'))return;
      const selected=profileReferenceValues();if(selected.length>=4)throw new Error('最多4张参考图');
      const asset=await api('/media',{data,title:file.name,collection:'画像参考'});selected.push(asset.url);
      if(profileEditor!==owner||!$('#profile-visual-references'))return;
      await loadProfileReferences(selected);if(profileEditor!==owner||!$('#profile-save-state'))return;profileDirty=true;$('#profile-save-state').textContent='参考图已上传，画像尚未保存';
    }catch(e){toast(e.message);}};fileInput.click();
  } else if (action === "profile-back") {
    if (profileDirty) {
      modal("画像还没有保存", "<p>你可以关闭此提示继续编辑，也可以放弃本次未保存的修改。</p>",
        '<button class="button" data-action="close">继续编辑</button><button class="button" data-action="profile-discard">放弃修改并返回</button>');
    } else location.hash = "profiles";
  } else if (action === "profile-discard") {
    profileDirty = false; profileEditor = null; closeModal(); location.hash = "profiles";
  } else if (action === "profile-create-post") {
    newProfileChoice = button.dataset.id;
    newPostDialog();
  }
}
document.addEventListener("input", event => {
  if (!event.target.dataset.profileField) return;
  profileDirty = true;
  $("#profile-save-state").textContent = "有未保存的修改";
});

const profileRevisionActions = {baseline:'升级时保留',create:'创建',edit:'编辑',restore:'恢复旧版本',duplicate:'复用创建',sync:'同步更新'};
let profileVersionDialog = null;
function profileVersionActive(dialog) { return profileVersionDialog===dialog && dialog.element.isConnected && $('#modal').open; }
function updateProfileState(saved) {
  state.profiles = [...(state.profiles || []).filter(p => p.id !== saved.id), saved];
  if (location.hash === '#profile/' + saved.id && !profileDirty) { profileEditor=null; drawProfileEditor(); }
  else if (location.hash === '#profiles') drawProfiles();
}
function profileVersionValue(value, field) {
  if (field === 'visual_references') return value?.length ? value.map(url=>`<img class="profile-history-image" src="${esc(url)}" alt="该版本参考图">`).join('') : '未选择';
  return esc(value || '未填写');
}
async function loadProfileRevision(dialog, version) {
  const sequence = ++dialog.sequence;
  $('#profile-history-detail').textContent='正在读取版本…';
  $('#profile-history-restore').disabled=true;
  const result=await api(`/profiles/${dialog.id}/history/${version}`);
  if (!profileVersionActive(dialog) || sequence!==dialog.sequence) return;
  dialog.selected=result;
  $('#profile-history-detail').innerHTML=`<p>当前 v${result.current.version}，所选 v${result.revision.version}。${result.changes.length ? `共 ${result.changes.length} 项不同。` : '内容相同，无需恢复。'}</p>`+
    result.changes.map(c=>`<section class="profile-history-change"><h3>${esc(c.label)}</h3><div class="profile-history-comparison"><div><strong>当前内容</strong><div class="workflow-preserve">${profileVersionValue(c.current,c.field)}</div></div><div><strong>所选版本</strong><div class="workflow-preserve">${profileVersionValue(c.selected,c.field)}</div></div></div></section>`).join('')+
    `<details><summary>查看所选版本全部字段</summary>${profileFields.map(([k,label])=>`<p><strong>${label}</strong><br><span class="workflow-preserve">${esc(result.revision.snapshot[k]||'未填写')}</span></p>`).join('')}</details>`;
  $('#profile-history-restore').disabled=!result.changes.length;
}
async function handleProfileVersions(action, button) {
  if (action.endsWith('-open')) {
    if (profileDirty && $('#profile-name')) throw new Error('请先保存画像修改，再查看历史或复用');
    const id=button.dataset.id;
    modal(action==='profile-history-open'?'画像版本历史':'复用为新画像','<div id="profile-version-dialog">正在读取…</div>');
    const dialog={id,element:$('#profile-version-dialog'),sequence:0}; profileVersionDialog=dialog;
    if (action==='profile-history-open') {
      const data=await api(`/profiles/${id}/history`);
      if (!profileVersionActive(dialog)) return;
      dialog.element.innerHTML=`<p><strong>${esc(data.profile.name)}</strong></p><p class="tip">历史从升级时的当前版本开始保留。恢复会创建新版本；独立长期记忆和已有任务快照保持各自记录。历史仅保存在本端。</p><label>选择版本<select id="profile-history-select">${data.revisions.map(r=>`<option value="${r.version}">v${r.version} · ${profileRevisionActions[r.action]||esc(r.action)} · ${esc(fmt(r.at))}</option>`).join('')}</select></label><div id="profile-history-detail"></div><div class="actions"><button id="profile-history-restore" class="button primary" data-action="profile-history-restore" disabled>确认恢复为新版本</button></div>`;
      $('#profile-history-select').onchange=()=>loadProfileRevision(dialog,Number($('#profile-history-select').value)).catch(modalError);
      await loadProfileRevision(dialog,data.revisions[0].version);
    } else {
      dialog.source=await api(`/profiles/${id}`);
      if (!profileVersionActive(dialog)) return;
      dialog.requestId=crypto.randomUUID().replaceAll('-','');
      dialog.element.innerHTML=`<p>来源：${esc(dialog.source.name)} · v${dialog.source.version}</p><p>将定位、读者、目标、写作偏好及视觉参考复制为独立画像。社媒主页、运营经验与反馈、长期记忆及文章不复制。来源之后的修改不会自动带入。</p><label>新画像名称<input id="profile-duplicate-name" maxlength="80" value="${esc((dialog.source.name+' · 副本').slice(0,80))}"></label><div class="actions"><button class="button primary" data-action="profile-duplicate-create">创建独立画像</button></div>`;
    }
    return;
  }
  const dialog=profileVersionDialog;
  if (!dialog || !profileVersionActive(dialog)) return;
  if (action==='profile-history-restore') {
    const selected=dialog.selected;
    if (!selected?.changes.length) return;
    if (profileDirty && $('#profile-name')) throw new Error('请先保存画像修改，再重新查看历史');
    const saved=await api(`/profiles/${dialog.id}/restore`,{version:selected.current.version,target_version:selected.revision.version});
    updateProfileState(saved);
    if (profileVersionActive(dialog)) closeModal();
    toast(`已恢复为 v${saved.version}，原版本历史保留`);
  } else if (action==='profile-duplicate-create') {
    const name=$('#profile-duplicate-name').value;
    // Keep the same request identifier after a lost response; changing the name starts a new request.
    if (dialog.submittedName!==undefined && dialog.submittedName!==name) dialog.requestId=crypto.randomUUID().replaceAll('-','');
    dialog.submittedName=name;
    const saved=await api(`/profiles/${dialog.id}/duplicate`,{version:dialog.source.version,name,request_id:dialog.requestId});
    updateProfileState(saved);
    if (profileVersionActive(dialog)) {closeModal();profileEditor=null;location.hash='profile/'+saved.id;}
    toast('独立画像已创建，请核对后用于创作');
  }
}
document.addEventListener('change',event=>{if(event.target.id==='profile-visual-references'){profileDirty=true;$('#profile-save-state').textContent='有未保存的修改';}});
