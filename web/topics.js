"use strict";

let topicDraft = {profile_id: "", idea: "", reference_ids: []}, topicRequestId = "", topicSequence = 0;
let topicBusy = false;
const topicLabels = {queued: "排队中", running: "正在生成选题", completed: "可选择", failed: "生成失败", interrupted: "生成中断"};
const topicFields = [["angle", "切入角度"], ["reader_value", "给谁看、有什么用"],
  ["profile_fit", "为什么适合这个账号"], ["evidence", "已有素材依据"], ["gaps", "还缺什么"], ["difference", "与已有文章的区别"]];

function topicHistory(plans) {
  return plans.map(p => `<div class="content-row"><div class="row-main"><h3><a href="#topics/${esc(p.id)}">${esc(p.idea || "根据参考文章选题")}</a></h3>
    <p>${esc(p.profile_name)} · v${p.profile_version} · ${fmt(p.created_at)}</p></div>
    <span class="tag">${p.selected_post_id ? "已选定" : esc(topicLabels[p.status])}</span></div>`).join("") || '<p class="tip">还没有选题记录。</p>';
}

async function drawTopics() {
  const route = location.hash, parts = route.split("/"), id = parts[1];
  const sequence = ++topicSequence;
  if (!id || id === "new") {
    if (!$("#topic-form")) {
      if (parts[2] && (state.profiles || []).some(p => p.id === parts[2])) topicDraft.profile_id = parts[2];
      const posts = activePosts();
      $("#main").innerHTML = heading("帮我选题", "结合账号定位和已有素材，比较下一篇适合写什么。") +
        `<section class="panel panel-body" id="topic-form"><div class="form-grid">
        <label>使用哪份画像<select id="topic-profile">${profileOptions(topicDraft.profile_id)}</select></label>
        <label>本次想法或素材<textarea id="topic-idea" rows="6" maxlength="12000" placeholder="例如：最近接好了 Agent 记忆，想写一篇读者能照着验证的文章。可以粘贴学习记录或素材摘要。">${esc(topicDraft.idea)}</textarea></label></div>
        <details><summary>选择参考文章（最多 6 篇）</summary><div class="topic-references">${posts.map(p =>
          `<label class="check-label"><input type="checkbox" data-topic-reference="${esc(p.id)}" ${topicDraft.reference_ids.includes(p.id) ? "checked" : ""}>${esc(p.title)} · ${esc(profileName(p.profile_id))}</label>`).join("") || '<p>内容库还没有文章，可以先在上面粘贴素材。</p>'}</div></details>
        <p class="tip">会参考本画像最近 30 篇文章的标题和摘要，帮助区分新旧内容。网页链接只作为线索；需要用到的内容请粘贴在上面。</p>
        <div id="topic-planned-model"></div>
        <div class="actions"><button class="button primary" data-action="topic-generate" ${!(state.profiles || []).length ? "disabled" : ""}>生成 3 个候选选题</button>
        <a class="text-link" href="#profiles">${(state.profiles || []).length ? "管理画像" : "先创建画像"}</a></div></section>
        <section class="panel panel-body"><h2>之前的选题</h2><div id="topic-history" aria-live="polite">正在读取…</div></section>`;
      if(typeof renderPlannedModel==='function')void renderPlannedModel($('#topic-planned-model'),'topic');
    }
    try {
      const data = await api("/topics");
      if (sequence === topicSequence && location.hash === route && $("#topic-history")) $("#topic-history").innerHTML = topicHistory(data.plans);
    } catch (error) {
      if (sequence === topicSequence && $("#topic-history")) $("#topic-history").textContent = error.message;
    }
    return;
  }
  try {
    const plan = await api("/topics/" + encodeURIComponent(id));
    if (sequence !== topicSequence || location.hash !== route) return;
    const context = plan.context;
    const renderKey = `${plan.id}:${plan.status}:${plan.updated_at}:${plan.selected_post_id || ""}`;
    if ($("#topic-detail")?.dataset.renderKey === renderKey) return;
    $("#main").innerHTML = heading("比较候选选题", `${esc(context.profile.name)} · 画像 v${context.profile.version}`,
      '<a class="button" href="#topics">再策划一轮</a>') +
      `<section class="panel panel-body" id="topic-detail" data-render-key="${esc(renderKey)}"><span class="tag blue" role="status">${plan.selected_post_id ? "已存入选题库" : esc(topicLabels[plan.status])}</span>
      <p class="workflow-preserve">${esc(context.idea || "根据所选参考文章策划")}</p>
      ${typeof modelProvenanceHtml==='function'?modelProvenanceHtml(plan.model_provenance):''}
      ${plan.error ? `<p role="alert">${esc(plan.error)}</p>` : ""}
      ${["queued", "running"].includes(plan.status) ? '<p>完成后会自动展示，离开页面也会保留本次任务。</p>' : ""}
      ${["failed", "interrupted"].includes(plan.status) ? `<button class="button" data-action="topic-retry" data-id="${esc(id)}">按原素材重试</button>` : ""}
      ${plan.selected_post_id ? `<p>已保存选题与原始素材，可以生成并修改提纲。</p><a class="button primary" href="#edit/${esc(plan.selected_post_id)}">打开已选题目</a><button class="button primary" data-action="workflow-open" data-id="${esc(plan.selected_post_id)}">生成提纲</button>` : ""}
      <details><summary>查看本轮使用的画像和素材</summary>${profileSummary(context.profile)}
      <p>参考文章：${context.references.map(p => esc(p.title)).join("、") || "未选择"}</p>
      <p>用于比较的同画像文章：${context.history.map(p => esc(p.title)).join("、") || "暂无；无法判断与旧文是否重复"}</p></details></section>
      <div class="cards topic-cards">${plan.candidates.map((candidate, index) => `<article class="idea-card">
        <span class="tag">候选 ${index + 1}${plan.selected_index === index ? " · 已选定" : ""}</span><h2>${esc(candidate.title)}</h2>
        ${topicFields.map(([field, label]) => `<div class="topic-field"><strong>${label}</strong><p class="workflow-preserve">${esc(candidate[field])}</p></div>`).join("")}
        ${!plan.selected_post_id ? `<button class="button primary" data-action="topic-select" data-id="${esc(id)}" data-index="${index}">选这个，存入选题库</button>` : ""}</article>`).join("")}</div>`;
  } catch (error) {
    if (sequence === topicSequence && location.hash === route) $("#main").innerHTML = empty("暂时无法读取选题", esc(error.message), '<a href="#topics">返回选题策划</a>');
  }
}

async function handleTopicAction(action, button) {
  if (topicBusy) return;
  topicBusy = true;
  button.disabled = true;
  try {
    if (action === "topic-generate") {
      if (!topicDraft.profile_id) throw new Error("请先选择画像");
      if (!topicDraft.idea.trim() && !topicDraft.reference_ids.length) throw new Error("请填写想法或选择参考文章");
      if (topicDraft.reference_ids.length > 6) throw new Error("最多选择 6 篇参考文章");
      $$("#topic-form input,#topic-form textarea,#topic-form select").forEach(el => el.disabled = true);
      if (!topicRequestId) topicRequestId = crypto.randomUUID().replaceAll("-", "");
      const plan = await api("/topics", {...topicDraft, request_id: topicRequestId});
      topicDraft = {profile_id: topicDraft.profile_id, idea: "", reference_ids: []};
      topicRequestId = "";
      location.hash = "topics/" + plan.id;
    } else if (action === "topic-retry") {
      await api("/topics/" + button.dataset.id + "/retry", {});
      await drawTopics();
    } else if (action === "topic-select") {
      await api("/topics/" + button.dataset.id + "/select", {index: Number(button.dataset.index)});
      await refresh(false);
      toast("已存入选题库，推荐理由保留在本轮记录中");
      await drawTopics();
    }
  } finally {
    topicBusy = false; button.disabled = false;
    $$("#topic-form input,#topic-form textarea,#topic-form select").forEach(el => el.disabled = false);
  }
}

document.addEventListener("input", event => {
  if (!event.target.closest("#topic-form")) return;
  topicDraft = {profile_id: $("#topic-profile").value, idea: $("#topic-idea").value,
    reference_ids: $$("[data-topic-reference]:checked").map(el => el.dataset.topicReference)};
  topicRequestId = "";
});
window.addEventListener("beforeunload", event => {
  if (topicDraft.idea || topicDraft.reference_ids.length) {event.preventDefault(); event.returnValue = "";}
});
