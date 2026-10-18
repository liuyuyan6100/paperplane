"use strict";
let workflowDeepLinkOpened = "";

// Shared app helpers are resolved when these functions run, after app.js loads.
const workflowLabels = {
  queued: ["排队中", "blue"],
  running: ["处理中", "blue"],
  outline_review: ["待确认提纲", "amber"],
  review: ["待审阅", "purple"],
  failed: ["失败", "red"],
  interrupted: ["执行中断", "amber"],
  canceled: ["已取消", ""],
  applied: ["已采用", "green"],
};
const workflowStageLabels = {
  prepare: "整理稿件与参考资料",
  outline: "生成提纲",
  outline_review: "等待确认提纲",
  generate: "调用模型处理正文",
  review: "检查结果与图片",
  apply: "保存采用结果",
  completed: "处理完成",
};
let workflowRows = [],
  workflowCurrent = null,
  workflowPostId = "",
  workflowPostVersion = null,
  workflowReferences = new Set(),
  workflowInstruction = "",
  workflowDialogSequence = 0,
  workflowPageSequence = 0,
  workflowPollTimer = null,
  workflowPollBusy = false,
  workflowPageFilter = "all",
  workflowPageQuery = "";
const workflowMutations = new Set();
let workflowParent = null;
// Draft text stays in this browser tab across closing/reopening the dialog and reloads.
function outlineDraft(task) {
  try { const draft = JSON.parse(sessionStorage.getItem("outline:" + task.id));
    if (draft?.version === task.outline_version) return draft.text;
  } catch (_) {}
  return task.outline || "";
}

function workflowBadge(status) {
  const label = workflowLabels[status] || [status || "未知状态", ""];
  return `<span class="tag ${label[1]}">${esc(label[0])}</span>`;
}

function workflowStage(task) {
  return task.stage_label || workflowStageLabels[task.stage] || "等待开始";
}

function workflowDialogMatches(mode, id = "") {
  const container = $("#workflow-dialog");
  return Boolean(
    $("#modal").open &&
      container &&
      container.dataset.mode === mode &&
      (!id || container.dataset.id === id),
  );
}

function stopWorkflowPolling() {
  clearTimeout(workflowPollTimer);
  workflowPollTimer = null;
}

function workflowHistoryRows(rows) {
  if (!rows.length)
    return '<div class="empty"><h3>还没有创作任务</h3><p>选择文章、补充要求，把正文交给已配置的模型处理。</p></div>';
  return rows
    .map(
      (task) => `<article class="workflow-history-row">
        <div class="row-main"><h3>${esc(task.post_title || state.posts.find((p) => p.id === task.post_id)?.title || "文章任务")}</h3>
        <div class="row-meta">${workflowBadge(task.status)}${task.profile ? `<span class="tag">${esc(task.profile.name)} · v${task.profile.version}</span>` : ""}<span>${esc(workflowStage(task))}</span><span>${fmt(task.created_at)}</span></div>
        <p class="workflow-excerpt">${esc(task.error || task.instruction || "正文处理")}</p></div>
        <button class="button small" data-action="workflow-show" data-id="${esc(task.id)}">${task.status === "review" ? "审阅结果" : "查看任务"}</button>
      </article>`,
    )
    .join("");
}

function renderWorkflowList() {
  const container = $("#workflow-page-list");
  if (!container || view !== "workflows") return;
  const rows = workflowRows.filter((task) => {
    const matchStatus =
      workflowPageFilter === "all" ||
      (workflowPageFilter === "active" &&
        ["queued", "running"].includes(task.status)) ||
      (workflowPageFilter === "failed" &&
        ["failed", "interrupted"].includes(task.status)) ||
      task.status === workflowPageFilter;
    const text = `${task.post_title || ""} ${task.instruction || ""}`;
    return (
      matchStatus &&
      text.toLowerCase().includes(workflowPageQuery.toLowerCase())
    );
  });
  container.innerHTML = workflowHistoryRows(rows);
  const count = $("#workflow-page-count");
  if (count)
    count.textContent = `显示 ${rows.length} / ${workflowRows.length} 个任务`;
}

async function drawWorkflows() {
  const request = ++workflowPageSequence;
  $("#main").innerHTML =
    heading(
      "创作任务",
      "工作台调用模型处理稿件，完成后由你审阅采用。",
      '<button class="button" data-action="workflow-refresh-list">↻ 刷新任务</button><button class="button primary" data-action="workflow-new">＋ 创建创作任务</button>',
    ) +
    `<section class="panel panel-body"><p class="tip">关闭弹窗或浏览器后，任务仍由本机服务执行。保留工作台服务运行，稍后可回到这里查看结果。</p>
      <div class="form-grid"><label>任务状态<select id="workflow-filter">
        ${[
          ["all", "全部任务"],
          ["active", "排队 / 处理中"],
          ["outline_review", "待确认提纲"],
          ["review", "待审阅"],
          ["failed", "失败 / 中断"],
          ["applied", "已采用"],
          ["canceled", "已取消"],
        ]
          .map(
            ([value, title]) =>
              `<option value="${value}" ${workflowPageFilter === value ? "selected" : ""}>${title}</option>`,
          )
          .join("")}
      </select></label><label>搜索文章或要求<input id="workflow-search" type="search" value="${esc(workflowPageQuery)}" placeholder="输入文章标题或任务要求"></label></div>
      <small class="muted" id="workflow-page-count">正在读取任务…</small><div id="workflow-page-list" class="workflow-history"></div>
    </section>`;
  try {
    const result = await api("/workflows");
    if (request !== workflowPageSequence || view !== "workflows") return;
    workflowRows = result.workflows;
    renderWorkflowList();
    const linkedTask = location.hash.split("/")[1] || "";
    if (/^[a-f0-9]{32}$/.test(linkedTask) && workflowDeepLinkOpened !== linkedTask) {
      workflowDeepLinkOpened = linkedTask;
      await openWorkflowTask(linkedTask);
    }

  } catch (error) {
    if (request !== workflowPageSequence || view !== "workflows") return;
    $("#workflow-page-count").textContent = "暂时无法读取任务";
    $("#workflow-page-list").innerHTML =
      `<p class="error-inline">${esc(error.message)}</p>`;
  }
}

function renderWorkflowReferences() {
  const container = $("#workflow-reference-list");
  if (!container) return;
  const query = ($("#workflow-reference-search")?.value || "")
    .trim()
    .toLowerCase();
  const rows = state.posts.filter(
    (post) =>
      !post.archived &&
      post.id !== workflowPostId &&
      `${post.title} ${post.summary || ""}`.toLowerCase().includes(query),
  );
  container.innerHTML = rows.length
    ? rows
        .map(
          (post) =>
            `<label class="check-label workflow-reference"><input type="checkbox" data-workflow-reference="${esc(post.id)}" ${workflowReferences.has(post.id) ? "checked" : ""} ${workflowReferences.size >= 6 && !workflowReferences.has(post.id) ? "disabled" : ""}><span><strong>${esc(post.title)}</strong><small>${esc(stageNames[post.stage] || "文章")} · ${post.body.length.toLocaleString()} 字 · v${post.version}</small></span></label>`,
        )
        .join("")
    : '<p class="tip">没有匹配的参考文章，可以直接处理当前稿件。</p>';
  $("#workflow-reference-count").textContent =
    `已选 ${workflowReferences.size} / 6 篇参考文章`;
}

async function loadWorkflowHistory(postId, sequence) {
  try {
    const result = await api(
      "/workflows?post_id=" + encodeURIComponent(postId),
    );
    if (
      sequence !== workflowDialogSequence ||
      !workflowDialogMatches("compose", postId)
    )
      return;
    $("#workflow-article-history").innerHTML = workflowHistoryRows(
      result.workflows,
    );
  } catch (error) {
    if (
      sequence !== workflowDialogSequence ||
      !workflowDialogMatches("compose", postId)
    )
      return;
    $("#workflow-article-history").innerHTML =
      `<p class="error-inline">任务历史读取失败：${esc(error.message)}</p>`;
  }
}

async function openWorkflowDialog(postId = editor?.id, preserve = false) {
  stopWorkflowPolling();
  if (!postId) return openWorkflowChooser();
  if (editor?.id === postId && dirty) {
    await saveEditor(true);
    if (dirty) throw new Error("稿件仍有未保存修改，请保存后再创建任务");
  }
  const post = state.posts.find((item) => item.id === postId);
  if (!post || post.archived)
    throw new Error("文章不存在或已归档，请回到内容库检查");
  const sequence = ++workflowDialogSequence;
  if (!preserve || workflowPostId !== postId) {
    workflowReferences = new Set();
    workflowInstruction =
      "请结合当前稿件和参考文章，整理结构并完善正文。保留有依据的技术结论与适用范围，缺少依据的内容标记为待核实，指出需要补充的真实截图。";
  }
  if (!preserve) workflowParent = null;
  workflowPostId = postId;
  workflowPostVersion = post.version;
  workflowCurrent = null;
  modal(
    workflowParent ? "调整素材和要求" : "生成提纲",
    `<div id="workflow-dialog" data-mode="compose" data-id="${esc(postId)}">
      <div class="notice blue"><strong>${esc(post.title)}</strong><p>使用已保存的 v${post.version} 稿件。任务会保存参考文章副本，在后台调用「渠道与设置」中选定的 API 或本地程序。</p></div>
      ${profileSummary((state.profiles || []).find(p => p.id === post.profile_id))}
      <p class="tip">提交时保存画像快照。后续修改画像不改变本次任务；未填写的运营信息按未知处理。</p>
      ${state.ai_ready ? "" : '<p class="notice">尚未配置辅助写作模型，请先到「渠道与设置」完成配置并测试。</p>'}
      <fieldset class="workflow-fieldset"><legend>参考文章 <small>可选，最多 6 篇</small></legend>
        <input id="workflow-reference-search" type="search" aria-label="搜索参考文章" placeholder="搜索文章标题或摘要">
        <div id="workflow-reference-list" class="workflow-reference-list"></div>
        <small id="workflow-reference-count" class="muted" aria-live="polite"></small>
      </fieldset>
      <label>处理要求<textarea id="workflow-instruction" rows="5" maxlength="3000" placeholder="希望解决什么问题、保留哪些内容、希望怎样组织文章…">${esc(workflowInstruction)}</textarea></label>
      <p class="tip">先生成提纲，你可以修改章节、要点和配图安排。确认提纲后才写正文，正文仍需审阅采用。</p>
      <div id="workflow-planned-model"></div>
      <details class="workflow-history-details"><summary>这篇文章的任务历史</summary><div id="workflow-article-history" class="workflow-history"><p class="tip">正在读取历史任务…</p></div></details>
    </div>`,
    '<button class="button" data-action="close">关闭</button><button class="button primary" data-action="workflow-create" ' +
      (state.ai_ready ? "" : "disabled") +
      ">" + (workflowParent ? "保留提纲，更新输入" : "生成提纲") + "</button>",
  );
  renderWorkflowReferences();
  if(typeof renderPlannedModel==='function')void renderPlannedModel($('#workflow-planned-model'),'outline');
  void loadWorkflowHistory(postId, sequence);
}

function openWorkflowChooser() {
  stopWorkflowPolling();
  workflowDialogSequence++;
  const posts = state.posts.filter((post) => !post.archived);
  modal(
    "选择要处理的文章",
    `<div id="workflow-dialog" data-mode="choose"><p class="tip">先选一篇目标文章，下一步选择参考资料并填写处理要求。</p>${posts.length ? `<label>目标文章<select id="workflow-post-choice">${posts.map((post) => `<option value="${esc(post.id)}">${esc(post.title)} · ${esc(stageNames[post.stage] || "文章")}</option>`).join("")}</select></label>` : "<p>内容库里还没有文章，请先创建内容。</p>"}</div>`,
    '<button class="button" data-action="close">关闭</button>' +
      (posts.length
        ? '<button class="button primary" data-action="workflow-choose">下一步</button>'
        : ""),
  );
}

function workflowReviewItems(items) {
  if (!Array.isArray(items))
    return typeof items === "string" && items ? [items] : [];
  return items.map((item) => {
    if (typeof item === "string") return item;
    if (item && typeof item === "object")
      return (
        item.message || item.description || item.title || JSON.stringify(item)
      );
    return String(item);
  });
}

function workflowBodyDraft(task) {
  const fallback={title:task.result?.title||task.selected_title||task.post_title,body:task.result?.body||''};
  try {const draft=JSON.parse(sessionStorage.getItem('workflow-body:'+task.id));if(task.status==='review'&&draft?.signature===task.result_signature)return draft.content;}catch(_){}
  return fallback;
}
let workflowBodySaving=false;
async function saveWorkflowBody(task) {
  if(task.status!=='review'||!$('#workflow-result-body'))return task;
  if(workflowBodySaving)throw new Error('待审正文正在保存，请稍候');
  const content={title:$('#workflow-result-title').value,body:$('#workflow-result-body').value};
  if(content.body===task.result.body&&content.title===(task.result.title||task.selected_title||task.post_title))return task;
  const controls=[$('#workflow-result-title'),$('#workflow-result-body')];workflowBodySaving=true;controls.forEach(n=>n.disabled=true);
  try {
    const next=await api('/workflows/'+task.id+'/result_edit',{result_signature:task.result_signature,content});
    sessionStorage.removeItem('workflow-body:'+task.id);updateWorkflowDetail(next);return next;
  }finally{workflowBodySaving=false;controls.forEach(n=>n.disabled=false);}
}
document.addEventListener('input',event=>{
  if(!['workflow-result-title','workflow-result-body'].includes(event.target.id)||workflowCurrent?.status!=='review')return;
  try{sessionStorage.setItem('workflow-body:'+workflowCurrent.id,JSON.stringify({signature:workflowCurrent.result_signature,content:{title:$('#workflow-result-title').value,body:$('#workflow-result-body').value}}));}catch(_){}
});

function renderWorkflowResult(task) {
  if (task.status === "outline_review") {
    let title=task.selected_title||task.post_title||'';
    try { const draft=JSON.parse(sessionStorage.getItem('outline:'+task.id));if(draft?.version===task.outline_version&&typeof draft.title==='string')title=draft.title; } catch (_) {}
    return `<label>本篇采用的标题 <small>可选提纲中的标题，也可直接修改；采用正文时同步到文章</small><input id="workflow-title" list="workflow-title-options" maxlength="150" value="${esc(title)}"><datalist id="workflow-title-options">${(task.title_options||[]).map(t=>`<option value="${esc(t)}"></option>`).join('')}</datalist></label><label>编辑提纲 <small>章节、要点、建议字数与配图安排</small><textarea id="workflow-outline" class="workflow-result" rows="16" maxlength="30000">${esc(outlineDraft(task))}</textarea></label><p class="tip" id="outline-save-state">修改后点“保存提纲”保存到工作台；点“确认标题、提纲并生成正文”会同时保存并开始写作。</p>`;
  }
  const result = task.result || {};
  const body = workflowBodyDraft(task).body;
  if (!body)
    return '<p class="tip">处理完成后，正文与审阅提醒会显示在这里。</p>';
  const review = result.review || {};
  const warnings = workflowReviewItems(review.warnings);
  const missing = workflowReviewItems(review.missing_images);
  const reminders =
    warnings.length || missing.length
      ? `<section class="notice workflow-review"><h3>采用前请检查</h3>${warnings.length ? `<ul>${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}${missing.length ? `<h4>需要补充的图片</h4><ul>${missing.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}</section>`
      : '<p class="tip">请核对事实、图片与引用，再采用正文。模型检查不能代替实际验证。</p>';
  return `${task.status==='review'?`<label>待审标题<input id="workflow-result-title" maxlength="150" value="${esc(workflowBodyDraft(task).title)}"></label>`:`<p><strong>采用标题：${esc(result.title||task.selected_title||task.post_title)}</strong></p>`}${task.outline ? `<details><summary>查看本次已确认的提纲</summary><p class="workflow-preserve">${esc(task.outline)}</p></details>` : ""}${reminders}<label>生成正文 <small>${body.length.toLocaleString()} 字 · 审阅后采用到原文章</small><textarea id="workflow-result-body" class="workflow-result" rows="15" ${task.status==='review'?'':'readonly'} spellcheck="false">${esc(body)}</textarea></label><p class="tip">采用会将上述标题与正文保存为新版本，原有版本仍可在「版本历史」查看。之后在文章编辑器补齐摘要、封面和正文配图，再准备公众号草稿。</p>`;
}

function renderWorkflowProgress(task) {
  const steps = ["生成提纲", "确认提纲", "生成正文", "审阅采用"];
  const index = task.status === "applied" ? 4 : task.status === "review" ? 3 : task.confirmed_outline_version ? 2 : task.status === "outline_review" ? 1 : 0;
  return `<ol class="workflow-progress" aria-label="处理阶段">${steps.map((label, i) => `<li class="${i < index ? "done" : i === index ? "active" : ""}"><span>${i < index ? "✓" : i + 1}</span>${label}</li>`).join("")}</ol>`;
}

function renderWorkflowDetail(task) {
  const source = state.posts.find((post) => post.id === task.post_id);
  const versionChanged =
    source && source.version !== task.post_version && task.status === "review";
  const referenceTitles = (task.reference_ids || []).map(
    (id) =>
      state.posts.find((post) => post.id === id)?.title ||
      "已保存的参考文章副本",
  );
  return `<div class="workflow-detail-heading"><div><h3>${esc(task.post_title || source?.title || "文章任务")}</h3><p class="muted">基于稿件 v${esc(task.post_version)} · ${fmt(task.created_at)} 创建</p></div>${workflowBadge(task.status)}</div>
    ${renderWorkflowProgress(task)}
    <div class="notice blue" role="status">${task.status==='outline_review'?'下一步：选择本篇标题、检查提纲，再确认生成正文。':task.status==='review'?'下一步：审阅标题和正文，确认后采用到文章。':task.status==='applied'?'正文已采用。下一步：打开图文编辑器，补齐摘要、封面、章节配图并准备公众号草稿。':'按当前阶段提示继续，已保存的文章与素材保留。'}</div>
    ${typeof modelProvenanceHtml==='function'?(task.model_stages||[task.model_provenance]).map(modelProvenanceHtml).join(''):''}
    ${task.status==='outline_review'?'<div id="workflow-body-planned"></div>':''}
    <div class="workflow-current-stage" role="status"><strong>${esc(workflowStage(task))}</strong>${["queued", "running"].includes(task.status) ? "<small>可以关闭窗口，任务会继续由工作台服务执行。</small>" : ""}</div>
    ${task.input_changes?.length && task.status !== "applied" ? `<p class="notice">${esc(task.input_changes.join("、"))}已变化。本任务使用修改前的输入；已有结果会保留。可点“调整素材和要求”基于最新输入继续。</p>` : ""}
    ${task.topic ? `<p class="tip">已带入选题时的想法、选择理由和素材；可在下方展开查看。</p>` : ""}
    ${task.error ? `<p class="error-inline">${esc(task.error)}</p>` : ""}
    ${task.status === "interrupted" ? '<p class="notice">上次执行已中断，结果不会自动采用。可在确认模型服务可用后重新执行。</p>' : ""}
    ${versionChanged ? `<p class="notice">原文章已更新到 v${source.version}，与任务使用的 v${task.post_version} 不同。当前结果会保留；请基于最新稿件新建任务，避免覆盖后续修改。</p>` : ""}
    <details class="workflow-input-details"><summary>查看任务要求与参考资料</summary><p class="workflow-preserve">${esc(task.instruction || "")}</p>${task.topic ? `<p class="workflow-preserve">选题想法：${esc(task.topic.idea)}\n选题方向：${esc(task.topic.candidate?.angle || "")}\n读者收益：${esc(task.topic.candidate?.reader_value || "")}</p><p class="tip">选题关联素材：${(task.topic.references || []).map(r => esc(r.title)).join("、") || "无"}</p>` : ""}<p class="tip">${referenceTitles.length ? `参考文章：${referenceTitles.map(esc).join("、")}` : "本次只使用目标稿件，没有选择其他参考文章。"}</p></details>
    ${profileSummary(task.profile)}
    ${task.creation_request?`<details><summary>本篇原始要求</summary><p class="workflow-preserve">${esc([...(task.creation_request.previous_user_messages||[]),task.creation_request.current_message||''].join('\n\n'))}</p></details>`:''}
    <div class="workflow-result-section">${renderWorkflowResult(task)}</div>`;
}

function renderWorkflowFooter(task) {
  return `<button class="button" data-action="close">关闭</button><button class="button" data-action="workflow-history">任务历史</button>
    ${task.status === "outline_review" ? `<button class="button" data-action="workflow-outline-save" data-id="${esc(task.id)}">保存提纲</button><button class="button primary" data-action="workflow-confirm" data-id="${esc(task.id)}">确认标题、提纲并生成正文</button>` : ""}
    ${task.outline ? `<button class="button" data-action="workflow-adjust" data-id="${esc(task.id)}">调整素材和要求</button>` : ""}
    ${["review", "applied"].includes(task.status) && task.confirmed_outline_version ? `<button class="button" data-action="workflow-regenerate" data-id="${esc(task.id)}">沿用提纲重写</button><button class="button" data-action="workflow-outline-revise" data-id="${esc(task.id)}">修改提纲后重写</button>` : ""}
    ${task.status === 'review' ? `<button class="button" data-action="workflow-body-save" data-id="${esc(task.id)}">保存待审正文</button>` : ''}
    ${task.status === "queued" ? `<button class="button danger" data-action="workflow-cancel" data-id="${esc(task.id)}">取消排队</button>` : ""}
    ${["failed", "interrupted"].includes(task.status) ? `<button class="button primary" data-action="workflow-retry" data-id="${esc(task.id)}">重新执行</button>` : ""}
    ${task.status === "review" && task.result?.body ? `<button class="button" data-action="workflow-compose" data-post-id="${esc(task.post_id)}">基于最新稿件新建</button><button class="button primary" data-action="workflow-apply" data-id="${esc(task.id)}" ${state.posts.some(p=>p.id===task.post_id&&p.version!==task.post_version)?'disabled':''}>采用标题与正文</button>` : ""}
    ${task.status === "applied" ? `<button class="button primary" data-action="workflow-edit" data-post-id="${esc(task.post_id)}">下一步：编辑图文与准备草稿</button>` : ""}`;
}

function updateWorkflowDetail(task) {
  if (!workflowDialogMatches("detail", task.id)) return;
  const inputsOpen = $("#workflow-detail .workflow-input-details")?.open;
  workflowCurrent = task;
  $("#workflow-detail").innerHTML = renderWorkflowDetail(task);
  if(['outline_review','review'].includes(task.status))assistantWrap($('#workflow-detail'),task.status==='outline_review'?'outline':'body',task.id);
  if (inputsOpen) $("#workflow-detail .workflow-input-details").open = true;
  $("#modal-content .modal-footer").innerHTML = renderWorkflowFooter(task);
  if(task.status==='outline_review'&&typeof renderPlannedModel==='function')void renderPlannedModel($('#workflow-body-planned'),'body');
}

function scheduleWorkflowPoll(task) {
  stopWorkflowPolling();
  if (!["queued", "running"].includes(task.status)) return;
  const sequence = workflowDialogSequence;
  workflowPollTimer = setTimeout(async () => {
    if (
      sequence !== workflowDialogSequence ||
      !workflowDialogMatches("detail", task.id)
    )
      return;
    if (document.hidden || workflowPollBusy || workflowMutations.has(task.id)) {
      scheduleWorkflowPoll(task);
      return;
    }
    workflowPollBusy = true;
    try {
      const next = await api("/workflows/" + encodeURIComponent(task.id));
      if (
        sequence !== workflowDialogSequence ||
        !workflowDialogMatches("detail", task.id)
      )
        return;
      $("#workflow-connection").textContent = "";
      updateWorkflowDetail(next);
      scheduleWorkflowPoll(next);
    } catch (error) {
      if (
        sequence !== workflowDialogSequence ||
        !workflowDialogMatches("detail", task.id)
      )
        return;
      $("#workflow-connection").textContent =
        `暂时无法更新状态：${error.message}。将继续尝试读取。`;
      scheduleWorkflowPoll(task);
    } finally {
      workflowPollBusy = false;
    }
  }, 2500);
}

async function openWorkflowTask(id, supplied = null) {
  stopWorkflowPolling();
  const sequence = ++workflowDialogSequence;
  modal(
    "创作任务",
    `<div id="workflow-dialog" data-mode="detail" data-id="${esc(id)}"><div id="workflow-detail"><p>正在读取任务…</p></div><p id="workflow-connection" class="tip" role="status"></p></div>`,
    '<button class="button" data-action="close">关闭</button>',
  );
  try {
    const task =
      supplied || (await api("/workflows/" + encodeURIComponent(id)));
    if (
      sequence !== workflowDialogSequence ||
      !workflowDialogMatches("detail", id)
    )
      return;
    updateWorkflowDetail(task);
    scheduleWorkflowPoll(task);
  } catch (error) {
    if (
      sequence !== workflowDialogSequence ||
      !workflowDialogMatches("detail", id)
    )
      return;
    $("#workflow-detail").innerHTML =
      `<p class="error-inline">${esc(error.message)}</p><button class="button" data-action="workflow-show" data-id="${esc(id)}">重新读取任务</button>`;
  }
}

async function applyWorkflowResult(task) {
  task = await saveWorkflowBody(task);
  if (editor?.id === task.post_id && dirty)
    throw new Error(
      "当前编辑器还有未保存修改，请先保存并核对稿件；任务结果会继续保留",
    );
  await refresh(false);
  if (editor?.id === task.post_id && dirty)
    throw new Error(
      "当前编辑器出现了未保存修改，请先保存并核对；任务结果会继续保留",
    );
  const latest = state.posts.find((post) => post.id === task.post_id);
  if (!latest) throw new Error("原文章不存在，任务结果已保留");
  if (latest.version !== task.post_version)
    throw new Error(
      `原文章已从 v${task.post_version} 更新为 v${latest.version}。为保留这些修改，请基于最新稿件新建任务；本次结果仍可查看。`,
    );
  const result = await api(
    "/workflows/" + encodeURIComponent(task.id) + "/apply",
    {
      version: task.post_version,
    },
  );
  const saved = result.post;
  state.posts = state.posts.map((post) =>
    post.id === saved.id ? saved : post,
  );
  const stillViewing = workflowDialogMatches("detail", task.id);
  if (editor?.id === saved.id && !dirty) {
    editor = saved;
    if (view === "edit") drawEditor();
  }
  if (stillViewing) {
    closeModal();
    if (view !== "edit" || editor?.id !== saved.id)
      location.hash = "edit/" + saved.id;
  }
  toast("已采用并保存正文，可继续配图与发布检查");
}

async function handleWorkflowAction(button) {
  const action = button.dataset.action;
  if (!action?.startsWith("workflow-")) return false;
  const id = button.dataset.id;
  if(action==='workflow-body-save') {
    await saveWorkflowBody(workflowCurrent);toast('待审正文已保存，原文章尚未更新');
  } else if (action === "workflow-open") {
    await openWorkflowDialog(id || editor?.id);
  } else if (["workflow-outline-save", "workflow-confirm"].includes(action)) {
    if (workflowMutations.has(id)) return true;
    workflowMutations.add(id);
    try {
      const task = await api(`/workflows/${id}/${action === "workflow-confirm" ? "confirm" : "outline_edit"}`, {
        outline: $("#workflow-outline").value, outline_version: workflowCurrent.outline_version,
        selected_title: $('#workflow-title').value,
      });
      try { sessionStorage.removeItem("outline:" + id); } catch (_) {}
      updateWorkflowDetail(task); scheduleWorkflowPoll(task);
      toast(action === "workflow-confirm" ? "提纲已确认，开始生成正文" : "提纲已保存");
    } finally { workflowMutations.delete(id); }
  } else if (action === "workflow-adjust") {
    const parent = workflowCurrent;
    // Preserve unsaved outline edits on the source task before branching.
    if (parent.status === "outline_review" && ($("#workflow-outline").value.trim() !== parent.outline || $('#workflow-title').value.trim() !== parent.selected_title)) {
      await api(`/workflows/${id}/outline_edit`, {outline: $("#workflow-outline").value, outline_version: parent.outline_version, selected_title:$('#workflow-title').value});
    }
    await refresh(false);
    workflowParent = parent; workflowPostId = parent.post_id;
    workflowReferences = new Set(parent.reference_ids); workflowInstruction = parent.instruction;
    await openWorkflowDialog(parent.post_id, true);
  } else if (["workflow-regenerate", "workflow-outline-revise"].includes(action)) {
    if (workflowMutations.has(id)) return true;
    workflowMutations.add(id);
    try {
      const parent = workflowCurrent;
      await refresh(false);
      const post = state.posts.find(p => p.id === parent.post_id);
      const task = await api("/workflows", {post_id: parent.post_id, version: post.version,
        reference_ids: parent.reference_ids, instruction: parent.instruction, parent_id: id,
        reuse_confirmed: action === "workflow-regenerate"});
      await openWorkflowTask(task.id, task);
      toast(task.status === "outline_review" ? "已保留提纲，请修改并确认" : "沿用已确认提纲重新生成正文");
    } finally {workflowMutations.delete(id);}
  } else if (action === "workflow-new") {
    openWorkflowChooser();
  } else if (action === "workflow-choose") {
    await openWorkflowDialog($("#workflow-post-choice").value);
  } else if (action === "workflow-compose") {
    await refresh(false);
    await openWorkflowDialog(button.dataset.postId);
  } else if (action === "workflow-refresh-list") {
    await drawWorkflows();
  } else if (action === "workflow-history") {
    stopWorkflowPolling();
    closeModal();
    if (view === "workflows") await drawWorkflows();
    else location.hash = "workflows";
  } else if (action === "workflow-show") {
    await openWorkflowTask(id);
  } else if (action === "workflow-edit") {
    closeModal();
    location.hash = "edit/" + button.dataset.postId;
  } else if (action === "workflow-create") {
    if (!workflowDialogMatches("compose", workflowPostId)) return true;
    if (workflowMutations.has("create")) return true;
    const post = state.posts.find((item) => item.id === workflowPostId);
    if (!post || post.archived)
      throw new Error("文章不存在或已归档，请回到内容库检查");
    const instruction = $("#workflow-instruction").value.trim();
    if (!instruction) throw new Error("请填写希望模型怎样处理这篇文章");
    if (workflowReferences.size > 6) throw new Error("参考文章最多选择 6 篇");
    workflowInstruction = instruction;
    const sequence = workflowDialogSequence;
    workflowMutations.add("create");
    try {
      const task = await api("/workflows", {
        post_id: post.id,
        version: workflowPostVersion,
        parent_id: workflowParent?.id || "",
        reference_ids: [...workflowReferences],
        instruction,
      });
      toast("创作任务已提交，可以稍后在「创作任务」查看结果");
      if (
        sequence === workflowDialogSequence &&
        workflowDialogMatches("compose", post.id)
      )
        await openWorkflowTask(task.id, task);
    } finally {
      workflowMutations.delete("create");
    }
  } else if (
    ["workflow-cancel", "workflow-retry", "workflow-apply"].includes(action)
  ) {
    if (workflowMutations.has(id)) return true;
    workflowMutations.add(id);
    // Discard any status read that began before this mutation.
    if (workflowDialogMatches("detail", id)) workflowDialogSequence++;
    try {
      if (action === "workflow-apply") {
        const task =
          workflowCurrent?.id === id
            ? workflowCurrent
            : await api("/workflows/" + encodeURIComponent(id));
        await applyWorkflowResult(task);
      } else {
        const operation = action === "workflow-cancel" ? "cancel" : "retry";
        const task = await api(
          "/workflows/" + encodeURIComponent(id) + "/" + operation,
          {},
        );
        toast(operation === "cancel" ? "已取消排队任务" : "任务已重新排队");
        updateWorkflowDetail(task);
        scheduleWorkflowPoll(task);
      }
    } finally {
      workflowMutations.delete(id);
      if (workflowDialogMatches("detail", id) && workflowCurrent)
        scheduleWorkflowPoll(workflowCurrent);
    }
  }
  return true;
}

document.addEventListener("input", (event) => {
  if (["workflow-outline", "workflow-title"].includes(event.target.id) && workflowCurrent) {
    let hint = "提纲尚未保存到工作台，请点保存或确认。";
    try { sessionStorage.setItem("outline:" + workflowCurrent.id, JSON.stringify({version: workflowCurrent.outline_version, text: $('#workflow-outline').value, title:$('#workflow-title').value})); hint = "修改暂存于本浏览器标签页；请点保存或确认，保存到工作台。"; } catch (_) {}
    $("#outline-save-state").textContent = hint;
  }
  if (event.target.id === "workflow-reference-search")
    renderWorkflowReferences();
  if (event.target.id === "workflow-search") {
    workflowPageQuery = event.target.value;
    renderWorkflowList();
  }
});
document.addEventListener("change", (event) => {
  const reference = event.target.dataset.workflowReference;
  if (reference) {
    if (event.target.checked) {
      if (workflowReferences.size >= 6) {
        event.target.checked = false;
        toast("参考文章最多选择 6 篇");
      } else workflowReferences.add(reference);
    } else workflowReferences.delete(reference);
    renderWorkflowReferences();
  }
  if (event.target.id === "workflow-filter") {
    workflowPageFilter = event.target.value;
    renderWorkflowList();
  }
});
document.addEventListener("DOMContentLoaded", () => {
  $("#modal").addEventListener("close", stopWorkflowPolling);
});
