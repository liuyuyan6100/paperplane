"use strict";
let queueDeepLinkOpened = "";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (d) =>
  d
    ? new Date(d).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "—";
const stageNames = { idea: "选题中", draft: "写作中", ready: "待发布", published: "已发布" };
const statuses = {
  queued: ["已排期", "blue"],
  running: ["执行中", "blue"],
  manual: ["待手动发表", "amber"],
  draft: ["平台草稿", "purple"],
  published: ["已发布", "green"],
  failed: ["失败", "red"],
  uncertain: ["结果待核对", "red"],
  canceled: ["已取消", ""],
};
const navs = [
  ["home", "◫", "今日概览"],
  ["guide", "?", "使用引导"],
  ["chat", "◌", "对话安排"],
  ["ideas", "◇", "选题素材"],
  ["content", "▤", "内容库"],
  ["library", "▣", "共享资料库"],
  ["profiles", "◎", "画像管理"],
  ["topics", "✦", "帮我选题"],
  ["workflows", "✧", "创作任务"],
  ["repurpose", "⇄", "平台改编"],
  ["illustrations", "◩", "配图工作台"],
  ["reviews", "✓", "审稿建议"],
  ["assets", "▧", "图片素材"],
  ["queue", "↗", "发布队列"],
  ["platforms", "▦", "渠道管理"],
  ["calendar", "▦", "排期日历"],
  ["analytics", "▥", "数据复盘"],
];
let state = { posts: [], jobs: [], channels: [], worker: {} },
  token = "",
  deployment = "local",
  view = "",
  filter = "all",
  query = "",
  editor = null,
  dirty = false,
  previewTimer,
  toastTimer,
  pollBusy = false;
let publishPost = null,
  report = null,
  reportSelection = "",
  activeJob = null,
  modalBusy = false,
  generation = 0;
const queueSelection = new Set();
let queueBatch = null,
  queuePreparation = 0;
const queueBatchLimit = 1000;
const activePosts = () => state.posts.filter((p) => !p.archived);
const activeJobs = () => state.jobs.filter((j) => !j.deleted_at);
const awaiting = () =>
  activeJobs().filter((j) =>
    ["manual", "draft", "failed", "uncertain"].includes(j.status),
  );
const badge = (status, stage = false) =>
  `<span class="tag ${stage ? { idea: "", draft: "blue", ready: "purple", published: "green" }[status] || "" : statuses[status]?.[1] || ""}">${esc(stage ? stageNames[status] : statuses[status]?.[0] || status)}</span>`;
const channelLogo = (id) =>
  `<span class="channel-logo ${esc(id)}">${{ local: "↗", wechat: "微", juejin: "掘", csdn: "C", xiaohongshu: "红", devto: "DEV" }[id] || "文"}</span>`;
const safeLink = (u) =>
  typeof u === "string" &&
  (/^https?:\/\//i.test(u) || /^\/read\/[a-f0-9]{32}$/.test(u))
    ? esc(u)
    : "#";

let stateEpoch=0, refreshSequence=0;
const stateUpdates=typeof BroadcastChannel!=='undefined'?new BroadcastChannel('paperplane-state'):null;
async function api(path, data, retrySession = true) {
  if(data!==undefined)stateEpoch++;
  const response = await fetch(
    "/api" + path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Studio-Token": token,
          },
          body: JSON.stringify(data),
        },
  );
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("连接或登录状态已变化。请保留编辑内容，在新窗口打开工作台重新登录后重试。");
  }
  if (response.status === 401 || response.redirected) {
    throw new Error("登录已过期。请保留编辑内容，在新窗口打开工作台重新登录后再保存。");
  }
  if (!response.ok) {
    if (
      response.status === 403 &&
      data !== undefined &&
      retrySession &&
      value.error?.includes("页面会话已更新")
    ) {
      token = (await api("/session")).token;
      return api(path, data, false);
    }
    const e = new Error(value.error || "操作失败");
    e.details = value.details;
    throw e;
  }
  if(data!==undefined){stateEpoch++;stateUpdates?.postMessage({changed:true});}
  return value;
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 4500);
}
function modal(title, body, footer = "") {
  queuePreparation++;
  queueBatch = null;
  $("#modal-content").innerHTML =
    `<div class="modal-head"><h2>${esc(title)}</h2><button class="close" data-action="close" aria-label="关闭对话框">×</button></div><div id="modal-error" role="alert" aria-live="assertive" tabindex="-1"></div><div class="modal-body">${body}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ""}`;
  if (!$("#modal").open) $("#modal").showModal();
}
function modalError(error) {
  const el = $("#modal-error");
  if (el) {
    el.innerHTML = `<div class="error-inline">${esc(error.message)}</div>`;
    $('#modal').scrollTop = 0;
    el.focus({preventScroll:true});
  } else toast(error.message);
}
function closeModal() {
  if (!modalBusy) {
    queuePreparation++;
    $("#modal").close();
    activeJob = null;
    publishPost = null;
    queueBatch = null;
  }
}
function heading(title, subtitle, actions = "") {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${subtitle}</p></div><div class="actions">${actions}</div></div>`;
}
function empty(title, description, action = "") {
  return `<div class="empty"><h3>${title}</h3><div>${description}</div>${action}</div>`;
}
const newButton =
  '<button class="button primary" data-action="new">＋ 新建内容</button>';

async function refresh(render = true) {
  const seq=++refreshSequence, epoch=stateEpoch;
  const latest=await api("/state");
  if(seq!==refreshSequence||epoch!==stateEpoch)return;
  state=latest;
  $("#service-state").innerHTML =
    `<span class="status-dot ${state.worker.alive && !state.worker.error ? "" : "offline"}"></span>${state.worker.error ? "调度异常，请检查服务" : state.worker.alive ? (deployment === "cloud" ? "云端服务运行中" : "本地服务运行中") : "调度器未启动"}`;
  const shouldRender = typeof render === "function" ? render() : render;
  if (shouldRender && !$("#job-quick-menu")?.matches(":popover-open") && !(view === "queue" && $("#modal").open)) draw();
  else drawNav();
}
function drawNav() {
  $("#nav").innerHTML =
    navs
      .map(
        ([id, icon, name]) =>
          `<a class="nav-item ${view === id ? "active" : ""}" href="#${id}" ${id === "guide" ? 'data-guide="open"' : ""} ${view === id ? 'aria-current="page"' : ""}><span class="nav-icon">${icon}</span>${name}${id === "queue" && awaiting().length ? `<span class="nav-count">${awaiting().length}</span>` : ""}</a>`,
      )
      .join("") +
    `<a class="nav-item settings-mobile ${view === "settings" ? "active" : ""}" href="#settings"><span class="nav-icon">⚙</span>设置</a>`;
}
function draw() {
  closeJobMenu();
  const route = location.hash.slice(1) || "home";
  view = route.split("/")[0];
  document.body.classList.toggle("desktop-update-page", view === "updates");
  drawNav();
  $("#breadcrumb").textContent =
    "工作台 / " +
    (navs.find((n) => n[0] === view)?.[2] ||
      { edit: "内容编辑", profile: "编辑画像", settings: "设置", platforms: "渠道管理" }[view] ||
      "今日概览");
  if (view === "edit") {
    const id = route.split("/")[1];
    if (editor?.id === id && $("#edit-title")) return;
    editor = state.posts.find((p) => p.id === id);
    if (!editor) {
      $("#main").innerHTML = empty("文章不存在", "请返回内容库重新打开");
      return;
    }
    dirty = false;
    drawEditor();
    return;
  }
  editor = null;
  dirty = false;
  (
    ({
      home: drawHome,
      guide: drawUsageGuide,
      chat: drawChat,
      ideas: drawIdeas,
      content: drawContent,
      library: drawSharedLibrary,
      profiles: drawProfiles,
      topics: drawTopics,
      profile: drawProfileEditor,
      assets: drawAssets,
      workflows: drawWorkflows,
      repurpose: drawRepurpose,
      illustrations: drawIllustrations,
      reviews: drawReviews,
      queue: drawQueue,
      calendar: drawCalendar,
      analytics: drawAnalytics,
      settings: drawSettings,
      platforms: drawPlatforms,
      updates: drawDesktopUpdates,
    })[view] || drawHome
  )();
}

function publicationBadge(p) {
  const progress=p.publication_progress;
  if(!progress?.published.length){
    const job=p.target_channel && platformDraftJobs(p).find(j=>['draft','uncertain'].includes(j.status));
    if(job?.publication_sync?.state==='reviewing')return '<span class="tag amber">平台审核中</span>';
    if(job?.status==='draft')return '<span class="tag blue">已送草稿 · 待发表</span>';
    return badge(p.stage,true);
  }
  const name=id=>state.channels.find(c=>c.id===id)?.name||id;
  return `<span class="tag green">${esc(progress.published.map(name).join('、'))}已发布</span>${progress.pending.length?`<span class="tag amber">${esc(progress.pending.map(name).join('、'))}待完成</span>`:''}${p.stage==='draft'?'<span class="tag blue">本地修改中</span>':''}`;
}
function postRow(p) {
  return `<div class="content-row"><div class="document-icon">▤</div><div class="row-main"><h3><button data-action="edit" data-id="${p.id}">${esc(p.title)}</button></h3><div class="row-meta">${p.source_post_id ? `<span class="tag blue">${esc(state.channels.find(c=>c.id===p.target_channel)?.name || p.target_channel)}平台稿</span>` : ""}${publicationBadge(p)}<span>${esc(p.tags.slice(0, 2).join(" / ") || "未分类")}</span><span>${fmt(p.updated_at)} 更新</span></div></div><div class="actions">${!p.source_post_id && p.publication_progress?.pending.length ? `<a class="button small primary" href="#repurpose/${p.id}">继续其他平台 →</a>` : ''}<button class="button small" data-action="edit" data-id="${p.id}">${p.stage === "ready" ? "查看稿件" : "继续写作"} →</button></div></div>`;
}
// Keep platform drafts accessible under their original article on the home page.
function creationFamilies(posts) {
  const active = posts.filter(p=>!p.archived);
  const ids = new Set(active.map(p=>p.id));
  const needsWork = p=>p.stage==='draft' || Boolean(p.publication_progress?.pending.length) || (p.stage==='ready' && !p.publication_progress?.complete);
  return active.filter(p=>!p.source_post_id || !ids.has(p.source_post_id)).map(p=>{
    const children=active.filter(c=>c.source_post_id===p.id);
    return {...p, platformDrafts:children, familyUpdatedAt:[p.updated_at,...children.map(c=>c.updated_at)].sort().at(-1)};
  }).filter(p=>needsWork(p)||p.platformDrafts.some(needsWork)).sort((a,b)=>b.familyUpdatedAt.localeCompare(a.familyUpdatedAt));
}
function creationFamilyRow(p) {
  const children=p.platformDrafts;
  return `<section class="creation-family">${postRow(p)}${children.length ? `<div class="platform-draft-list"><p class="tip">本篇平台稿 · 修改后保存在原平台稿中</p>${children.map(c=>`<div class="platform-draft-item"><div><span class="tag blue">${esc(state.channels.find(x=>x.id===c.target_channel)?.name || c.target_channel)}</span> ${publicationBadge(c)}<p>${esc(c.title)}</p>${platformDraftLinks(c)}</div><button class="button small" data-action="platform-draft-menu" data-id="${esc(c.id)}" aria-label="平台操作：${esc(c.title)}" aria-haspopup="menu" aria-expanded="false">操作 ▾</button></div>`).join('')}</div>` : ''}</section>`;
}
function platformDraftJobs(p) {
  return state.jobs.filter(j=>!j.deleted_at && j.post_id===p.id && j.channel_id===p.target_channel);
}
function platformDraftLinks(p) {
  const jobs=platformDraftJobs(p), published=jobs.find(j=>j.status==='published' && j.public_url);
  const draft=jobs.find(j=>j.status==='draft' && j.url);
  const link=(label,url)=>`<a class="platform-receipt-link" href="${safeLink(url)}" target="_blank" rel="noopener noreferrer">${label} ↗ <span>${esc(url)}</span></a>`;
  const result=published ? link('查看已发表文章',published.public_url) : draft ? link('查看平台草稿',draft.url) : '<span class="tip">暂无文章或草稿链接</span>';
  const sync=(draft || jobs.find(j=>j.publication_sync?.supported))?.publication_sync;
  return result+(sync?.supported && !published ? `<p class="tip" role="status">${esc(sync.message || '发表后自动核对状态和文章链接')}${sync.checked_at ? ' · '+fmt(sync.checked_at) : ''}</p>` : '');
}
function platformDraftMenu(p) {
  const jobs=platformDraftJobs(p), pending=jobs.find(j=>!['published','canceled'].includes(j.status)), published=jobs.find(j=>j.status==='published');
  const j=pending || published;
  const item=(action,label,id=p.id,extra='')=>`<button role="menuitem" data-action="${action}" data-id="${esc(id)}" ${extra}>${label}</button>`;
  let html=item('edit','编辑平台稿');
  if(j) html+=item('job',pending ? '查看任务 / 处理结果' : '查看发表记录',j.id);
  if(pending?.status==='draft' && pending.channel_kind==='bridge')html+=item('publishing-open','在纸飞机打开草稿',pending.id,`data-platform="${esc(p.target_channel)}"`);
  if(pending?.status==='draft' && pending.channel_kind==='xhs-browser')html+=item('xhs-browser-draft','打开小红书草稿',pending.id);
  if(pending?.publication_sync?.supported)html+=item('publication-recheck','立即同步发表状态',pending.id);
  if(pending && ['manual','draft','uncertain'].includes(pending.status))html+=item('quick-confirm-job',pending.publication_sync?.supported?'手动确认已发布（备用）…':'确认已公开发布…',pending.id);
  if(!pending && !published)html+=item('platform-draft-publish','检查并送稿…')+item('platform-draft-register','确认已公开发布…');
  if(published)html+=item('platform-public-link',published.public_url?'修改文章链接…':'补充文章链接…',published.id);
  return html;
}
function jobRow(j) {
  return `<div class="content-row">${channelLogo(j.channel_id)}<div class="row-main"><h3>${esc(j.title)}</h3><div class="row-meta"><span>${esc(j.channel_name)}</span>${j.legacy_key && j.status === "canceled" ? '<span class="tag">历史存档</span>' : badge(j.status)}<span>${esc(j.error || j.note || fmt(j.due_at))}</span></div></div>${jobMenuButton(j)}</div>`;
}
function jobMenuButton(j) {
  return `<button class="button small" data-action="job-menu" data-id="${esc(j.id)}" aria-label="处理：${esc(j.title)}" aria-haspopup="menu" aria-expanded="false">处理 ▾</button>`;
}
let jobMenuTrigger = null;
function closeJobMenu(restoreFocus = false) {
  const menu = $("#job-quick-menu");
  if (menu?.matches(":popover-open")) menu.hidePopover();
  if (jobMenuTrigger?.isConnected) {
    jobMenuTrigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) jobMenuTrigger.focus();
  }
}
function showJobMenu(button, platform = false) {
  let menu = $("#job-quick-menu");
  const wasOpen = menu?.matches(":popover-open") && jobMenuTrigger === button;
  closeJobMenu();
  if (wasOpen) return;
  const j = state.jobs.find((item) => item.id === button.dataset.id);
  const p = platform && state.posts.find(item=>item.id===button.dataset.id);
  if (platform ? !p || p.archived : !j || j.deleted_at) throw new Error("记录已变化，请刷新后查看");
  if (!menu) {
    menu = document.createElement("div");
    menu.id = "job-quick-menu";
    menu.className = "job-quick-menu";
    menu.setAttribute("popover", "auto");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "发布记录操作");
    menu.addEventListener("toggle", () => {
      if (jobMenuTrigger?.isConnected)
        jobMenuTrigger.setAttribute("aria-expanded", String(menu.matches(":popover-open")));
    });
    document.body.append(menu);
  }
  menu.setAttribute('aria-label', platform ? '平台稿操作' : '发布记录操作');
  menu.innerHTML = platform ? platformDraftMenu(p) : `<button role="menuitem" data-action="job" data-id="${esc(j.id)}">查看详情</button>
    ${j.channel_kind === 'xhs-browser' && j.status === 'draft' ? `<button role="menuitem" data-action="xhs-browser-draft" data-id="${esc(j.id)}">打开小红书草稿</button>` : ''}
    ${j.channel_kind === 'bridge' && j.status === 'draft' ? `<button role="menuitem" data-action="publishing-open" data-platform="${esc(j.channel_id)}" data-id="${esc(j.id)}">在纸飞机打开草稿</button>` : ''}
    ${["manual", "draft", "uncertain"].includes(j.status) ? `<button role="menuitem" data-action="quick-confirm-job" data-id="${esc(j.id)}">确认已发布</button>` : ""}
    <button role="menuitem" class="danger" data-action="delete-job" data-id="${esc(j.id)}" ${j.status === "running" ? 'disabled title="任务正在执行，完成后才能删除"' : ""}>删除</button>`;
  jobMenuTrigger = button;
  menu.showPopover();
  button.setAttribute("aria-expanded", "true");
  const anchor = button.getBoundingClientRect(), box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(anchor.right - box.width, innerWidth - box.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(anchor.bottom + 6, innerHeight - box.height - 8))}px`;
  menu.querySelector("button:not(:disabled)").focus();
}

async function showQuickConfirmation(id) {
  const j = await api("/jobs/" + id);
  if (j.deleted_at || !["manual", "draft", "uncertain"].includes(j.status)) {
    await refresh();
    throw new Error("当前状态不能确认发布，请查看最新详情");
  }
  modal("确认已发布",
    `<h3>${esc(j.snapshot.title)}</h3><p class="tip">${esc(j.channel_name)} · ${esc(statuses[j.status]?.[0] || j.status)}</p>
    <p>在平台完成公开发表后，在这里登记。此操作只更新工作台记录。</p>
    ${j.public_url ? `<a class="text-link" href="${safeLink(j.public_url)}" target="_blank" rel="noopener">查看文章 ↗</a>` : `<p>暂未找到公开链接，也可以直接确认已发布。</p>`}<label>公开文章链接（可选）<input id="confirmation-url" type="url" value="${esc(j.public_url || '')}" placeholder="https://…"></label>`,
    `<button class="button" data-action="close">取消</button><button class="button primary" data-action="quick-confirm-submit" data-id="${esc(j.id)}">确认已发布</button>`);
  $('[data-action="quick-confirm-submit"]').focus();
}
function drawHome() {
  const posts = activePosts(),
    jobs = activeJobs(),
    counts = [
      posts.filter((p) => p.stage === "idea").length,
      posts.filter((p) => p.stage === "draft").length,
      jobs.filter((j) =>
        [
          "queued",
          "running",
          "manual",
          "draft",
          "uncertain",
          "failed",
        ].includes(j.status),
      ).length,
      state.jobs.filter((j) => j.status === "published").length,
    ];
  const editingFamilies = creationFamilies(posts);
  const editing = editingFamilies.slice(0, 4);
  $("#main").innerHTML =
    heading(
      "把灵感，变成下一篇好内容。",
      "从选题到发布，在这里完成每一步。",
      `<a class="button primary" href="#chat">用对话安排任务</a><button class="button" data-action="new-idea">＋ 记个选题</button>${newButton}`,
    ) +
    usageGuideEntry() +
    `<div class="stats-grid">${[
      ["选题素材", "◇", "等待展开的灵感"],
      ["正在创作", "▤", "继续打磨你的内容"],
      ["发布待办", "↗", "排期、人工收尾与异常"],
      ["已发布", "✓", "已完成的渠道分发"],
    ]
      .map(
        (s, i) =>
          `<div class="stat-card"><div class="stat-top">${s[0]}<span class="stat-symbol">${s[1]}</span></div><div class="stat-number">${counts[i]}</div><small>${s[2]}</small></div>`,
      )
      .join("")}</div>` +
    `<div class="home-grid"><div><section class="panel"><div class="panel-head"><h2>继续创作 <span class="tag blue">${editingFamilies.length}</span></h2><a class="text-link" href="#content">查看内容库 →</a></div>${editing.length ? editing.map(creationFamilyRow).join("") : empty("留一点时间，写下你想分享的事", "从新建内容开始，也可以导入一篇 Markdown。", newButton)}</section>
  <section class="panel"><div class="panel-head"><h2>需要你处理 <span class="tag amber">${awaiting().length}</span></h2><a class="text-link" href="#queue">发布队列 →</a></div>${awaiting().length ? awaiting().slice(0, 4).map(jobRow).join("") : empty("暂时没有待处理事项", "排期到点会自动执行，人工渠道会在这里提醒。")}</section>
  <section class="panel"><div class="panel-head"><h2>你的内容发布流程</h2><span class="muted"><small>一步一步，有迹可循</small></span></div><div class="flow-strip">${[
    ["收集选题", "记下链接与想法"],
    ["完成创作", "编辑正文与预览"],
    ["检查与分发", "选择渠道和时间"],
    ["记录与复盘", "汇总发布和效果"],
  ]
    .map(
      (s, i) =>
        `<div class="flow-step"><span class="step-number">0${i + 1}</span><b>${s[0]}</b><small>${s[1]}</small></div>`,
    )
    .join("")}</div></section></div>
  <aside class="home-aside"><section class="panel"><div class="panel-head"><h2>发布渠道</h2><small>已启用 ${state.channels.filter(c=>c.enabled).length} / 已收录 ${state.channels.length}</small><a class="text-link" href="#platforms">管理</a></div><div class="panel-body">${state.channels.filter(c=>c.enabled).map((c) => `<div class="channel-row">${channelLogo(c.id)}<div class="channel-info"><b>${esc(c.name)}</b>${platformDetails(c)}<small>${c.kind === "local" ? (deployment === "cloud" ? "站内发布 · 登录可读" : "本机发布 · 即时可读") : c.kind === "manual" ? "导出内容 · 手动发表" : c.config.publish ? "API · 公开发布" : "API · 送入草稿"}</small></div><span class="tag ${(c.auto_send || c.kind === "local") ? "green" : ""}">${!c.enabled ? "已停用" : c.auto_send ? "已验通" : c.kind === "local" ? "本机" : c.ready ? "待验通" : "待配置"}</span></div>`).join("")}</div></section>
  <section class="panel"><div class="panel-head"><h2>接下来发布</h2><a class="text-link" href="#calendar">日历 →</a></div><div class="panel-body">${
    jobs
      .filter((j) => j.status === "queued")
      .sort((a, b) => a.due_at.localeCompare(b.due_at))
      .slice(0, 3)
      .map(
        (j) =>
          `<div class="channel-row"><span class="channel-logo">◷</span><div class="channel-info"><b>${esc(j.title)}</b><small>${fmt(j.due_at)} · ${esc(j.channel_name)}</small></div></div>`,
      )
      .join("") ||
    '<div class="tip">还没有排期。文章写好后，可检查并送入公众号草稿箱；其他平台使用文章页的「检查与分发」。</div>'
  }</div></section>
  <div class="tip-box tip"><strong>让工作台接着做</strong><br>在编辑器选择「生成提纲 / 写正文」，后台调用你配置的模型。去「创作任务」查看结果，审阅后再送入公众号草稿箱。</div></aside></div>`;
}

function contentToolbar(kind) {
  return `<div class="toolbar"><div class="tabs">${[
    ["all", "全部"],
    ["idea", "选题中"],
    ["draft", "写作中"],
    ["ready", "待发布"],
    ["published", "已发布"],
    ["archived", "已归档"],
  ]
    .map(
      ([id, n]) =>
        `<button class="tab ${filter === id ? "active" : ""}" data-action="filter" data-value="${id}">${n}</button>`,
    )
    .join(
      "",
    )}</div><input class="search" id="search" type="search" placeholder="搜索标题、正文或标签…" aria-label="搜索内容" value="${esc(query)}"></div>`;
}
function filteredPosts(ideas = false) {
  return state.posts.filter(
    (p) =>
      (filter === "archived" ? p.archived : !p.archived) &&
      (filter === "all" || filter === "archived" || (filter === "published" ? (p.publication_progress?.published.length || p.stage === "published") : filter === "ready" ? (p.publication_progress?.pending.length || (p.stage === "ready" && !p.publication_progress?.complete)) : p.stage === filter)) &&
      (!ideas || p.stage === "idea") &&
      (p.title + " " + p.body + " " + p.tags.join(" "))
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
}
function drawContent() {
  $("#main").innerHTML =
    heading(
      "内容库",
      "每一篇文章，从这里出发。",
      `<button class="button" data-action="import-md">导入 Markdown</button>${newButton}`,
    ) +
    contentToolbar() +
    `<section class="panel">${
      filteredPosts().length
        ? filteredPosts()
            .map(
              (p) =>
                `<div class="content-row"><div class="document-icon">▤</div><div class="row-main"><h3><button data-action="edit" data-id="${p.id}">${esc(p.title)}</button></h3><div class="row-meta">${publicationBadge(p)}<span class="tag">${esc(profileName(p.profile_id))}</span><span>${p.body.length.toLocaleString()} 字</span><span>${esc(p.tags.join(" / "))}</span><span>${fmt(p.updated_at)}</span></div></div><div class="actions"><button class="button small" data-action="${p.archived ? "restore" : "edit"}" data-id="${p.id}">${p.archived ? "恢复" : "编辑"}</button>${!p.archived ? `<button class="button ghost small" data-action="archive" data-id="${p.id}">归档</button>` : ""}</div></div>`,
            )
            .join("")
        : empty("这里还没有内容", "新建内容，或试试其他筛选条件。", newButton)
    }</section>`;
}
function drawIdeas() {
  const ideas = filteredPosts(true);
  $("#main").innerHTML =
    heading(
      "选题素材",
      "先记下来，让好想法有地方生长。",
      '<button class="button primary" data-action="new-idea">＋ 记录选题</button>',
    ) +
    `<div class="toolbar"><span class="muted"><small>${activePosts().filter((p) => p.stage === "idea").length} 个选题等待展开</small></span><input class="search" id="search" type="search" placeholder="搜索选题或标签…" aria-label="搜索选题" value="${esc(query)}"></div><div class="cards">${ideas.map((p) => `<article class="idea-card"><div class="actions">${p.tags.map((t) => `<span class="tag blue">${esc(t)}</span>`).join("") || '<span class="tag">灵感速记</span>'}</div><h3>${esc(p.title)}</h3><p>${esc((p.summary || p.body || "还没有补充素材。").slice(0, 110))}</p><div class="card-footer"><span>${fmt(p.updated_at)}</span><button class="text-link" data-action="start-writing" data-id="${p.id}">开始写作 →</button></div></article>`).join("")}</div>${!ideas.length ? empty("下一篇，从一个想法开始", "记录主题、来源链接和想表达的观点。", '<button class="button primary" data-action="new-idea">＋ 记录选题</button>') : ""}`;
}

function drawEditor() {
  const p = editor;
  $("#main").innerHTML =
    `<div class="page-heading editor-heading"><div><div class="actions"><a href="#content" class="text-link">← 内容库</a><span id="editor-stage">${publicationBadge(p)}</span><span id="save-state" class="save-state">已保存 · v${p.version}</span></div></div><div class="actions"><button class="button" data-action="register-publication">登记已发布</button><button class="button" data-action="repurpose-open">平台改编</button><button class="button" data-action="review-open">AI 审稿</button><button class="button" data-action="history">版本历史</button><button class="button" data-action="save">保存稿件</button>${p.target_channel && p.target_channel !== "wechat" ? '<button class="button primary" data-action="publish">检查与分发 →</button>' : '<button class="button primary" data-action="wechat-draft">检查并送入公众号草稿箱 →</button>'}</div></div>
 ${p.source_post_id ? `<div class="notice">${esc(platformNames()[p.target_channel] || p.target_channel)} 平台稿 · 基于母稿 v${p.source_version}。<a href="#edit/${p.source_post_id}">查看母稿</a>${state.posts.find(s => s.id === p.source_post_id)?.version !== p.source_version ? ' · 母稿已变化，请核对当前内容。' : ''}</div>` : ''}
 ${!p.target_channel || p.target_channel === "wechat" ? '<section id="article-flow" class="panel article-flow" aria-live="polite"><p class="tip">正在读取本篇流程…</p></section>' : ""}
 <section id="article-learning" class="panel panel-body spaced" aria-live="polite"></section>
 <section id="article-videos" class="panel spaced"></section><div class="editor-grid"><div class="editor-main"><section class="panel"><input id="edit-title" class="editor-title" aria-label="文章标题" placeholder="为你的文章起个标题" maxlength="150" value="${esc(p.title)}"><div class="editor-toolbar"><div class="actions"><span class="tag blue">Markdown</span><button class="button ghost small" data-action="template">插入提纲</button><button class="button ghost small" data-action="review-format">修复多余空行</button><button class="button ghost small" data-action="review-undo-format">恢复修复前正文</button><button class="button ghost small" data-action="upload-body">＋ 图片</button><button class="button ghost small" data-video-action="open">＋ 视频</button><button class="button ghost small" data-action="asset-picker">素材库</button><button class="button ghost small" data-action="illustration-open">✧ 配图工作台</button><button class="button ghost small" data-action="diagram">流程示意图</button><button class="button ghost small" data-action="xhs-open">小红书图文</button><button class="button primary small" data-action="workflow-open">✧ 生成提纲 / 写正文</button></div><small class="muted">正文 / 实时预览</small></div><div class="editor-panes"><textarea id="edit-body" aria-label="Markdown 正文" spellcheck="false" placeholder="写下你的正文…&#10;&#10;支持标题、列表、引用、代码块和图片。">${esc(p.body)}</textarea><div id="preview" class="preview prose"></div></div><div class="editor-foot"><span id="word-count">${p.body.length.toLocaleString()} 字 · 约 ${Math.max(1, Math.ceil(p.body.length / 400))} 分钟阅读</span><span>Ctrl / ⌘ + S 保存</span></div></section></div>
 <aside class="editor-settings"><section class="panel"><div class="panel-head"><h2>文章信息</h2></div><div class="panel-body"><label>内容阶段<select id="edit-stage">${Object.entries(
   stageNames,
 )
   .map(
     ([k, v]) =>
       `<option value="${k}" ${p.stage === k ? "selected" : ""}>${v}</option>`,
   )
   .join(
     "",
   )}</select></label><label>账号画像<select id="edit-profile">${profileOptions(p.profile_id)}</select><small>本篇创作使用所选画像的运营目标、读者和偏好。</small></label><label>摘要<textarea id="edit-summary" rows="4" maxlength="300" placeholder="一句话，说清这篇文章的价值">${esc(p.summary)}</textarea></label><label>作者<input id="edit-author" maxlength="60" value="${esc(p.author)}" placeholder="你的署名"></label><label>标签<input id="edit-tags" value="${esc(p.tags.join(", "))}" placeholder="用逗号分隔，例如：工具, 写作"></label><label>来源 / 参考<input id="edit-source" value="${esc(p.source)}" placeholder="链接或来源备注"></label></div></section><section class="panel"><div class="panel-head"><h2>封面图片</h2></div><div class="panel-body">${p.cover ? `<img class="cover-preview" src="${esc(p.cover)}" alt="文章封面">` : ""}<label>图片地址<input id="edit-cover" value="${esc(p.cover)}" placeholder="https://… 或上传图片"></label><button class="button wide" data-action="upload-cover">上传封面</button><p class="tip spaced">本地图片可随发布包下载。通过 API 发布到 DEV 时，请使用公开图片地址。</p></div></section></aside></div>`;
  $('.editor-settings').insertAdjacentHTML('afterbegin',assistantHost('post',p.id));
  const context=document.createElement('details');context.className='editor-context';
  context.innerHTML='<summary>本篇创作进度、视频素材与画像学习 <small>展开查看</small></summary>';
  const grid=$('#main .editor-grid');grid.before(context);
  ['article-flow','article-learning','article-videos'].forEach(id=>{const node=document.getElementById(id);if(node)context.append(node);});
  preview();
  void loadArticleVideos(p.id).catch(e=>toast(e.message));
  refreshArticleFlow();
  void loadArticleLearning(p.id);
}
function editorValues() {
  return {
    version: editor.version,
    title: $("#edit-title").value,
    body: $("#edit-body").value,
    summary: $("#edit-summary").value,
    author: $("#edit-author").value,
    profile_id: $("#edit-profile").value || null,
    tags: $("#edit-tags")
      .value.split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean),
    source: $("#edit-source").value,
    cover: $("#edit-cover").value,
    stage: $("#edit-stage").value,
  };
}
function markDirty() {
  dirty = true;
  $("#save-state").textContent = "有未保存的修改";
  const n = $("#edit-body").value.length;
  $("#word-count").textContent =
    `${n.toLocaleString()} 字 · 约 ${Math.max(1, Math.ceil(n / 400))} 分钟阅读`;
}
async function preview() {
  const body = $("#edit-body")?.value;
  if (body === undefined) return;
  const current = ++generation;
  try {
    const result = await api("/preview", { body });
    if (current === generation && $("#preview"))
      $("#preview").innerHTML =
        result.html || '<p class="muted">你的内容预览会显示在这里。</p>';
  } catch (e) {
    if ($("#preview")) $("#preview").textContent = e.message;
  }
}
async function saveEditor(silent = false) {
  if (!editor) return;
  if (!dirty) {
    if (!silent) toast("稿件已保存");
    return editor;
  }
  const submitted = editorValues(),
    id = editor.id;
  const saved = await api("/posts/" + id, submitted);
  if (editor?.id === id) {
    const current = editorValues();
    delete current.version;
    const comparison = { ...submitted };
    delete comparison.version;
    editor = saved;
    const assistant=document.querySelector('.editor-assistant[data-assistant-kind="post"]');
    if(assistant)await assistantRefresh(assistant);
    void loadArticleLearning(saved.id);
    dirty = JSON.stringify(current) !== JSON.stringify(comparison);
    $("#save-state").textContent = dirty
      ? "有未保存的修改"
      : `已保存 · v${editor.version}`;
    $("#editor-stage").innerHTML = badge(editor.stage, true);
  }
  state.posts = state.posts.map((p) => (p.id === saved.id ? saved : p));
  await refresh(false);
  if(editor?.id===id){editor={...editor,publication_progress:state.posts.find(p=>p.id===id)?.publication_progress};$("#editor-stage").innerHTML=publicationBadge(editor);}
  if (!silent) toast("稿件已保存");
  return saved;
}

function jobRemovalButton(j, small = false) {
  const size = small ? " small" : "";
  return j.deleted_at
    ? `<button class="button${size}" data-action="restore-job" data-id="${j.id}">恢复</button><button class="button danger${size}" data-action="purge-job" data-id="${j.id}" ${j.status === "running" ? "disabled" : ""}>彻底删除</button>`
    : `<button class="button danger${size}" data-action="delete-job" data-id="${j.id}" ${j.status === "running" ? 'disabled title="任务正在执行，完成后才能删除"' : ""}>删除</button>`;
}
function visibleQueueJobs() {
  const trashed = filter === "trash";
  return state.jobs.filter(
    (j) =>
      (trashed ? !!j.deleted_at : !j.deleted_at) &&
      (trashed ||
        filter === "all" ||
        (filter === "pending"
          ? ["manual", "draft", "failed", "uncertain"].includes(j.status)
          : j.status === filter)) &&
      (j.title + " " + j.channel_name)
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
}
function drawQueue() {
  const focused = document.activeElement,
    focusSelector =
      focused?.id === "queue-select-all"
        ? "#queue-select-all"
        : focused?.dataset.queueSelect
          ? `[data-queue-select="${CSS.escape(focused.dataset.queueSelect)}"]`
          : null;
  const trashed = filter === "trash",
    jobs = visibleQueueJobs(),
    selectable = jobs.filter((j) => j.status !== "running"),
    visibleIds = new Set(selectable.map((j) => j.id)),
    trashCount = state.jobs.filter((j) => j.deleted_at).length;
  for (const id of queueSelection)
    if (!visibleIds.has(id)) queueSelection.delete(id);
  const count = queueSelection.size;
  $("#main").innerHTML =
    heading(
      "发布队列",
      "每个渠道独立记录，一眼找到下一步。",
      '<button class="button" data-action="refresh">↻ 刷新状态</button>',
    ) +
    `<div class="notice blue">${
      trashed
        ? "回收站里的记录可以继续保留、恢复或彻底删除。恢复已取消的排期不会自动发布；彻底删除会清除队列日志和统计，原稿、素材与已发布文章保留。"
        : "服务运行时排期自动执行，关闭页面不影响任务。云端实例由 VPS 持续执行；各渠道按任务中保存的方式交付，人工渠道生成发表待办。"
    }</div>
    <div class="toolbar"><div class="tabs">${[
      ["all", "全部"],
      ["pending", "待处理"],
      ["queued", "已排期"],
      ["published", "已发布"],
      ["failed", "失败"],
      ["canceled", "已取消"],
      ["trash", `回收站${trashCount ? " · " + trashCount : ""}`],
    ]
      .map(
        ([k, v]) =>
          `<button class="tab ${filter === k ? "active" : ""}" data-action="filter" data-value="${k}">${v}</button>`,
      )
      .join("")}</div>
    <input class="search" id="search" type="search" value="${esc(query)}" placeholder="搜索文章或渠道…" aria-label="搜索发布任务"></div>
    <div class="queue-selection-bar">
      <div class="queue-selection-info"><label class="check-label"><input type="checkbox" id="queue-select-all" ${selectable.length && count === selectable.length ? "checked" : ""} ${!selectable.length ? "disabled" : ""}>全选当前结果</label><span class="tip" role="status">已选 ${count} 条 / 当前 ${jobs.length} 条</span></div>
      <div class="actions">${
        trashed
          ? `<button class="button" data-action="batch-restore-jobs" ${!count ? "disabled" : ""}>恢复所选</button><button class="button danger" data-action="batch-purge-jobs" ${!count ? "disabled" : ""}>彻底删除所选</button><button class="button danger" data-action="empty-trash" ${!trashCount ? "disabled" : ""}>清空回收站</button>`
          : `<button class="button danger" data-action="batch-delete-jobs" ${!count ? "disabled" : ""}>批量删除</button>`
      }
        ${count ? '<button class="button ghost" data-action="clear-queue-selection">取消选择</button>' : ""}</div>
    </div>
    <section class="panel table-wrap queue-panel">${
      jobs.length
        ? `<table class="queue-table"><thead><tr><th>选择 / 发布内容</th><th>渠道</th><th>状态</th><th>${trashed ? "删除时间" : "计划时间"} · 本机时区</th><th>操作</th></tr></thead><tbody>${jobs
            .map(
              (j) => `<tr>
      <td class="title-cell"><label class="queue-item-title"><input type="checkbox" data-queue-select="${j.id}" aria-label="选择：${esc(j.title)} · ${esc(j.channel_name)}" ${queueSelection.has(j.id) ? "checked" : ""} ${j.status === "running" ? 'disabled title="执行中的任务不能批量操作"' : ""}><span><b>${esc(j.title)}</b><small>${j.legacy_key ? "旧版记录" : "稿件 v" + j.version} · ${j.attempts} 次执行${j.legacy_key ? " · 旧版导入" : ""}</small></span></label></td>
      <td data-label="渠道"><div class="actions">${channelLogo(j.channel_id)}<span>${esc(j.channel_name)}</span></div></td>
      <td data-label="状态">${j.legacy_key && j.status === "canceled" ? '<span class="tag">历史存档</span>' : badge(j.status)}</td>
      <td class="nowrap" data-label="${trashed ? "删除时间" : "计划时间"}"><small>${fmt(trashed ? j.deleted_at : j.due_at)}</small></td>
      <td><div class="job-tools">${trashed ? `<button class="button small" data-action="job" data-id="${j.id}">查看详情</button>${jobRemovalButton(j, true)}` : jobMenuButton(j)}</div></td>
    </tr>`,
            )
            .join("")}</tbody></table>`
        : empty(
            query ? "没有匹配的记录" : trashed ? "回收站为空" : "暂无发布任务",
            query
              ? "试试其他文章标题或渠道名称，或清除搜索条件。"
              : trashed
                ? "删除的发布记录会保留在这里，方便恢复。"
                : "在编辑页完成检查，就能立即发布或设置排期。",
          )
    }</section>`;
  $("#queue-select-all").indeterminate = count > 0 && count < selectable.length;
  if (focusSelector) $(focusSelector)?.focus({ preventScroll: true });
  const linkedTask = location.hash.split("/")[1] || "";
  if (/^[a-f0-9]{32}$/.test(linkedTask) && queueDeepLinkOpened !== linkedTask) {
    queueDeepLinkOpened = linkedTask;
    void showJob(linkedTask).catch(error => toast(error.message));
  }
}
async function showQueueBatch(action, ids, emptying = false) {
  modal(
    "正在读取发布记录",
    "<p>正在核对所选记录的最新状态…</p>",
    '<button class="button" data-action="close">取消</button>',
  );
  const preparation = queuePreparation;
  await refresh(false);
  if (preparation !== queuePreparation || !$("#modal").open) return;
  if (emptying) ids = state.jobs.filter((j) => j.deleted_at).map((j) => j.id);
  ids = [...new Set(ids)];
  if (!ids.length) throw new Error("请先选择要处理的记录");
  if (ids.length > queueBatchLimit)
    throw new Error(
      `每次最多处理 ${queueBatchLimit} 条，请缩小筛选范围或分批选择`,
    );
  const jobs = ids.map((id) => state.jobs.find((j) => j.id === id));
  if (
    jobs.some(
      (j) =>
        !j ||
        j.status === "running" ||
        (action === "delete" ? !!j.deleted_at : !j.deleted_at),
    )
  ) {
    queueSelection.clear();
    draw();
    throw new Error("部分记录状态已变化，本次尚未操作。请刷新后重新选择");
  }
  const purging = action === "purge",
    queued = jobs.filter((j) => j.status === "queued").length,
    title = emptying
      ? "清空回收站"
      : {
          delete: "批量删除发布记录",
          restore: "恢复发布记录",
          purge: "彻底删除发布记录",
        }[action];
  modal(
    title,
    `<p>本次${emptying ? "清空整个回收站中的" : "处理所选"} <strong>${ids.length} 条</strong>发布记录。${emptying ? "包含当前搜索条件之外的记录。" : ""}</p>
    <ul class="queue-confirm-list">${jobs.map((j) => `<li><b>${esc(j.title)}</b><small>${esc(j.channel_name)} · ${esc(statuses[j.status]?.[0] || j.status)}</small></li>`).join("")}</ul>
    ${
      purging
        ? '<div class="notice">将永久清除这些队列记录、执行日志、统计、备注及任务稿件快照，不能从回收站恢复。原稿、图片和已经发布的文章保留，也不会撤回平台文章。</div>'
        : action === "delete"
          ? `<p>移到回收站后可以恢复，已有文章和统计会保留。</p>${queued ? `<div class="notice">其中 ${queued} 条未执行的排期将同时取消；恢复后不会自动发布。</div>` : ""}`
          : "<p>恢复到发布队列，保留原来的处理状态。已取消的排期仍为已取消，需要重新创建发布任务才会发送。</p>"
    }
    ${purging ? `<label class="check-label spaced"><input type="checkbox" id="purge-ack">我确认永久删除这 ${ids.length} 条发布记录</label>` : ""}`,
    `<button class="button" data-action="close">取消</button><button class="button ${purging || action === "delete" ? "danger" : "primary"}" id="confirm-queue-batch" data-action="confirm-queue-batch" ${purging ? "disabled" : ""}>${purging ? "确认彻底删除" : action === "delete" ? "移到回收站" : "确认恢复"}</button>`,
  );
  queueBatch = Object.freeze({ action, ids: Object.freeze(ids) });
}
function drawCalendar() {
  const jobs = activeJobs()
      .filter((j) =>
        ["queued", "running", "manual", "draft"].includes(j.status),
      )
      .sort((a, b) => a.due_at.localeCompare(b.due_at)),
    groups = {};
  jobs.forEach((j) => {
    const day = new Date(j.due_at).toLocaleDateString("sv-SE");
    (groups[day] ??= []).push(j);
  });
  $("#main").innerHTML =
    heading(
      "排期日历",
      "看清接下来要发布什么，把节奏留在自己手里。",
      `<a class="button" href="#content">选择文章排期</a>`,
    ) +
    `<div class="notice blue">时间按当前设备时区显示：${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}。保留待人工发表与平台草稿，直到实际完成。</div><div class="calendar-list">${
      Object.entries(groups)
        .map(
          ([day, items]) =>
            `<section class="schedule-day"><div class="day-label"><strong>${esc(day.slice(5).replace("-", " / "))}</strong>${esc(day.slice(0, 4))}</div><div class="panel">${items.map((j) => `<div class="content-row"><span class="tag blue">${new Date(j.due_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}</span><div class="row-main"><h3>${esc(j.title)}</h3><div class="row-meta">${esc(j.channel_name)} ${j.legacy_key && j.status === "canceled" ? '<span class="tag">历史存档</span>' : badge(j.status)}</div></div><button class="button small" data-action="job" data-id="${j.id}">查看</button></div>`).join("")}</div></section>`,
        )
        .join("") ||
      empty("日历还是空的", "选好文章和发布时间，就能安排接下来的内容。")
    }</div>`;
}
function drawAnalytics() { return drawPerformance(); }

function programSettings(settings) {
  return `<label>调用方式<select id="ai-mode"><option value="api" ${settings.ai_mode !== "program" ? "selected" : ""}>模型接口（API）</option><option value="program" ${settings.ai_mode === "program" ? "selected" : ""} ${settings.ai_program_allowed === false ? "disabled" : ""}>${settings.ai_program_allowed === false ? "服务器 Agent（尚未安装配置）" : "Agent / 本地程序（使用程序自身登录）"}</option></select></label>
  <div id="ai-program-fields" ${settings.ai_mode === "program" ? "" : "hidden"}>
  <p class="tip">程序必须支持非交互输入与输出，仅安装聊天窗口还不够。调用使用该程序自己的账号及额度。请只配置你信任的程序，它以当前系统用户权限运行。</p>
  ${(settings.ai_programs || []).map((p) => `<button class="button" data-action="program-preset" data-preset="${esc(JSON.stringify(p))}">使用 ${esc(p.name)}</button>`).join("")}
  <label>程序路径<input id="ai-program" value="${esc(settings.ai_program)}" placeholder="例如 C:/程序目录/node.exe"><small>Windows 使用 .exe；脚本路径放在参数的第一项。</small></label>
  <label>参数（JSON 数组）<textarea id="ai-program-args" rows="6">${esc(settings.ai_program_args)}</textarea><small>例如 ["脚本路径", "-p"]。稿件通过 UTF-8 标准输入传入，不需要占位符。</small></label>
  <label>输出格式<select id="ai-program-output"><option value="text" ${settings.ai_program_output === "text" ? "selected" : ""}>UTF-8 纯文本正文</option><option value="json" ${settings.ai_program_output === "json" ? "selected" : ""}>JSON（result 字段为正文）</option></select></label>
  <label>超时（秒）<input id="ai-program-timeout" type="number" min="5" max="300" value="${esc(settings.ai_program_timeout)}"></label></div>`;
}

let settingsDrawSequence = 0;
async function drawSettings() {
  const settingsSequence = ++settingsDrawSequence;
  $("#main").innerHTML = heading(
    "渠道与设置",
    "数据留在本地，发布方式由你选择。",
  );
  try {
    const [settings, legacy, modelSettings, bridge, xhsBrowser] = await Promise.all([
      api("/settings"),
      api("/legacy"),
      api("/model-settings"),
      api("/publishing-bridge"),
      api('/xhs-drafts'),
    ]);
    if (view !== "settings" || settingsSequence !== settingsDrawSequence) return;
    $("#main").innerHTML =
      heading(
        "渠道与设置",
        "数据留在本地，发布方式由你选择。",
        '<button class="button primary" data-action="save-settings">保存设置</button>',
      ) +
      modelSettingsPanel(modelSettings, settings) + xhsDraftSettings(xhsBrowser) + publishingBridgePanel(bridge) +
      `<div class="settings-grid"><div><section class="panel"><div class="panel-head"><h2>发布渠道</h2><a class="text-link" href="#platforms">渠道管理 →</a></div><div class="panel-body">${settings.channels.filter(c=>c.enabled || ['wechat','devto'].includes(c.id)).map((c) => `<div class="setting-channel">${channelLogo(c.id)}<div class="channel-info"><b>${esc(c.name)}</b>${platformDetails(c)}<small>${c.kind === "local" ? (deployment === "cloud" ? "保存到云端专栏，登录后阅读" : "无需配置，仅在本机发布") : c.kind === "xhs-browser" ? "自动保存图文草稿，打开后由你确认发布" : c.kind === "bridge" ? "浏览器自动送入草稿箱，尚不支持公开发表" : c.kind === "manual" ? "生成发布包，在平台手动发表" : c.kind === "wechat" ? "调用公众号接口，自动送入草稿箱" : "使用 API Key 独立调用官方接口"}</small></div>${c.id === "devto" ? `<select id="devto-publish" class="inline-select" aria-label="DEV 发布方式"><option value="false" ${!c.config.publish ? "selected" : ""}>送入草稿</option><option value="true" ${c.config.publish ? "selected" : ""}>公开发布</option></select>` : ""}<a class="text-link" href="#platforms">${c.enabled ? "已启用 · 管理" : "未启用 · 管理"}</a></div>`).join("")}<label class="spaced">DEV API Key<input type="password" id="devto-key" autocomplete="new-password" placeholder="${settings.devto_key_set ? "已配置，留空保留原值" : "输入 DEV 账号的 API Key"}"><small>在 DEV → Settings → Extensions 生成。密钥不会出现在页面数据或内容备份里。</small></label></div></section>
 ${wechatSettings(settings)}</div>
 <div><section class="panel"><div class="panel-head"><h2>数据与备份</h2></div><div class="panel-body"><label>当前数据目录<small>${esc(settings.data_dir)}</small></label><div class="tip">SQLite 保存文章、版本、排期、执行记录与复盘数据。备份包含数据库及上传图片，凭据单独保存在运行工作台的设备上。</div><button class="button wide spaced" data-action="backup">↓ 下载完整备份</button><p class="tip spaced">恢复备份：关闭服务后，按 README 中的恢复命令执行。恢复前会自动保存现有数据副本。</p><hr class="section-divider"><h3>接回旧版内容</h3><p class="tip">${legacy.available ? `已找到 ${esc(legacy.name)}：${legacy.ideas} 条选题、${legacy.publish} 条发布记录。只导入内容和历史记录，不连接旧服务、不导入凭据或自动执行旧任务。` : "当前项目未找到旧版内容备份。"}</p><button class="button wide" data-action="migrate" ${legacy.available ? "" : "disabled"}>导入旧版内容备份</button><small class="muted">可重复执行，已导入记录会跳过。</small></div></section><section class="panel"><div class="panel-head"><h2>独立运行</h2></div><div class="panel-body tip"><p><strong>纸飞机 v${esc(settings.version)}</strong></p>${settings.deployment === "cloud" ? "<p>当前工作台运行在 VPS，由 Docker Compose 管理。手机和电脑访问同一地址，共用文章、素材和发布队列。</p><p>关闭手机或电脑不会停止云端任务。点击页面上方「安装到桌面」查看手机安装方式。</p>" : "<p>双击项目中的「启动工作台.cmd」，或运行 <code>python run.py</code>。</p><p>保持电脑和服务运行，排期会自动执行。关闭浏览器不影响任务。</p>"}<p>凭据保护：${esc(settings.credential_protection)}。环境变量优先于页面配置。</p><a class="text-link" href="/feed" target="_blank" rel="noopener">打开本地专栏 →</a></div></section>${desktopUpdatePanel()}</div></div>`;
    rememberUsageSettings();
    refreshDesktopUpdates();
  } catch (e) {
    if (view === "settings")
      $("#main").innerHTML +=
        `<div class="error-inline">${esc(e.message)}</div>`;
  }
}

function newPostDialog(idea = false) {
  modal(
    idea ? "记录一个选题" : "新建内容",
    `<label>账号画像<select id="new-profile">${profileOptions(newProfileChoice)}</select></label><label>标题<input id="new-title" maxlength="150" placeholder="你想写什么？" autofocus></label><label>${idea ? "素材与想法" : "摘要（选填）"}<textarea id="new-summary" rows="4" maxlength="${idea ? 200000 : 300}" placeholder="${idea ? "记下观点、摘录和准备解决的问题…" : "这篇文章会给读者带来什么？"}"></textarea></label><div class="form-grid"><label>标签<input id="new-tags" placeholder="用逗号分隔"></label><label>来源（选填）<input id="new-source" placeholder="来源链接或备注"></label></div>`,
    `<button class="button" data-action="close">取消</button><button class="button primary" data-action="create" data-idea="${idea}">${idea ? "保存选题" : "创建并写作"}</button>`,
  );
}
async function openPublish(savedPost = null) {
  if (!savedPost) {
    await saveEditor(true);
    if (dirty) throw new Error("保存期间稿件发生变化，请先保存最新内容");
  }
  publishPost = savedPost || editor;
  report = null;
  reportSelection = "";
  const p = publishPost;
  modal(
    "检查与发布",
    `<h3>${esc(p.title)} <span class="tag">v${p.version}</span></h3><p class="tip">发布使用当前已保存的稿件快照，后续编辑不会改变本次任务。</p>${p.stage !== "ready" ? '<div class="notice">请先把内容阶段设为「待发布」并保存，再进行发布检查。</div>' : ""}<label>选择发布渠道</label><div class="channel-choices">${state.channels.filter(c=>c.enabled || c.id===p.target_channel).map((c) => `<label class="channel-choice"><input type="checkbox" name="publish-channel" value="${c.id}" ${c.id === (p.target_channel || "local") && c.enabled ? "checked" : ""} ${!c.enabled || !c.ready || (p.target_channel && c.id !== p.target_channel) ? "disabled" : ""}>${channelLogo(c.id)}<span>${esc(c.name)}<small>${esc(c.capability_label)}</small><small>${!c.ready ? "需配置" : !c.enabled ? "已停用" : c.kind === "manual" ? "手动发表" : c.kind === "local" ? "发布到本机" : c.config.publish ? "公开发布" : "送入草稿"}</small></span></label>`).join("")}</div><label>发布时间<select id="publish-timing"><option value="now">立即执行</option><option value="later">定时执行</option></select></label><label id="schedule-field" class="hidden">计划时间 · ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}<input type="datetime-local" id="publish-due"></label><div id="publish-checks"></div><label id="ack-field" class="check-label hidden"><input type="checkbox" id="ack-warnings">我已阅读并确认上面的提醒</label>`,
    `<button class="button" data-action="check">运行发布检查</button><button class="button primary" data-action="enqueue" id="enqueue-button" disabled>创建发布任务</button>`,
  );
  await runCheck();
}
function selectedChannels() {
  return $$("[name=publish-channel]:checked").map((x) => x.value);
}
async function runCheck() {
  const selection = selectedChannels();
  report = await api("/posts/" + publishPost.id + "/check", {
    channels: selection,
    version: publishPost.version,
  });
  if(report.post){publishPost=report.post;acceptRepairedPost(report.post);}
  if(report.repaired)toast('多余空行已自动修复并重新检查，修复前正文已备份');
  reportSelection = selection.join(",");
  $("#publish-checks").innerHTML =
    `<div class="checks">${report.checks.map((c) => `<div class="check-row ${c.level}"><span class="check-icon">${c.level === "pass" ? "✓" : c.level === "warn" ? "!" : "×"}</span><div><strong>${esc(c.label)}</strong><small>${esc(c.detail)}</small></div></div>`).join("")}</div>`;
  $("#ack-field").classList.toggle(
    "hidden",
    !report.checks.some((c) => c.level === "warn"),
  );
  $("#enqueue-button").disabled = !report.ok;
}

async function showJob(id, refreshOnly = false) {
  const received = await api("/jobs/" + id);
  if (refreshOnly && (!$('#modal').open || activeJob?.id !== id)) return;
  activeJob = received;
  const j = activeJob,
    p = j.snapshot,
    manual =
      !j.deleted_at && ["manual", "draft", "uncertain"].includes(j.status);
  modal(
    j.deleted_at ? "已删除的发布记录" : "发布任务",
    `${j.deleted_at ? `<div class="notice blue">这条记录已移到回收站（${fmt(j.deleted_at)}）。恢复后可继续处理；已取消的排期不会自动执行。</div>` : ""}<h3>${esc(p.title)}</h3><div class="actions">${channelLogo(j.channel_id)}<span>${esc(j.channel_name)}</span>${j.legacy_key && j.status === "canceled" ? '<span class="tag">历史存档</span>' : badge(j.status)}<span class="tag">${j.legacy_key ? "旧版记录" : "稿件 v" + j.version}</span></div>${j.legacy_key ? '<div class="notice spaced">旧版备份只有发布记录，未包含文章正文。下载完整发布包前，请在内容库补充原始 Markdown。</div>' : ""}<p class="tip spaced">计划 ${fmt(j.due_at)} · 执行 ${j.attempts} 次${j.legacy_key ? " · 从旧版备份导入" : ""}</p>${j.error ? `<div class="notice">${esc(j.error)}</div>` : ""}${!j.deleted_at && j.status === "manual" && j.channel_id !== "xiaohongshu" ? '<div class="notice blue">① 复制正文或下载发布包　② 打开平台编辑器发表　③ 回来确认已发布，链接可留空。公众号建议使用「复制排版」，本地图片需要在平台重新上传。</div>' : ""}
 ${xhsDraftJob(j)}
 ${!j.deleted_at && ['uncertain','draft'].includes(j.status) && ['cnblogs','oschina','segmentfault','51cto','toutiao','bilibili-article','zhihu'].includes(j.channel_id) && p.desktop_submission ? `<div class="notice blue">可回读原草稿核对内容；不会再次上传或提交。</div><label>原草稿编号<input id="draft-recheck-id" value="${esc(j.external_id || '')}" inputmode="numeric" placeholder="从平台草稿编辑地址复制编号"></label><button class="button" data-action="draft-recheck" data-id="${esc(j.id)}">核对这份草稿</button>` : ''}
 ${j.publication_sync?.supported ? `<div class="notice blue" role="status">${esc(j.publication_sync.message || '平台确认发表后，自动核对状态并关联文章链接。')}${j.publication_sync.checked_at ? '<br>最近核对：'+fmt(j.publication_sync.checked_at) : ''}</div>${['draft','uncertain'].includes(j.status) ? `<button class="button spaced" data-action="publication-recheck" data-id="${esc(j.id)}">↻ 立即同步发表状态</button>` : ''}` : ''}
 <details><summary>下载或手动处理</summary><div class="actions"><a class="button small" href="/api/jobs/${j.id}/package" download>↓ 下载发布包</a>${j.channel_id === "xiaohongshu" ? '<button class="button small" data-action="xhs-copy-title">复制小红书标题</button><button class="button small" data-action="xhs-copy-caption">复制小红书文案</button><p class="tip">下载后按图片编号上传，01 是封面。确认平台裁切和话题后发表，再确认已发布，链接可留空。</p>' : '<button class="button small" data-action="copy-md">复制 Markdown</button><button class="button small" data-action="copy-html">复制排版</button>'}${p.delivery_config?.editor_url && p.delivery_config?.mode !== "xhs-browser" ? `<a class="button small" href="${safeLink(p.delivery_config.editor_url)}" target="_blank" rel="noopener">打开平台 ↗</a>` : ""}${j.url ? `<a class="button small" href="${safeLink(j.url)}" target="_blank" rel="noopener">查看文章 ↗</a>` : ""}</div></details>
 ${j.channel_id === "xiaohongshu" ? `<h3>${esc(p.xhs_delivery?.title || p.xhs?.title)}</h3><p class="asset-description">${esc(p.xhs?.caption)}</p><div class="asset-grid">${(p.xhs?.photos || []).map((src, i) => `<article class="asset-card"><img src="${esc(src)}" alt="${i === 0 ? "封面" : `配图 ${i + 1}`}"><div>${i === 0 ? "01 · 封面" : String(i + 1).padStart(2, "0")}</div></article>`).join("")}</div>` : ""}
 ${manual ? `${j.publication_sync?.supported ? '<details class="spaced"><summary>自动核对未成功？手动补记发布结果</summary>' : ''}<hr class="section-divider"><label>公开链接（选填）<input id="job-url" type="url" placeholder="没有链接也可直接确认" value="${esc(j.public_url || "")}"><small>已有文章链接会自动关联；找不到链接可留空。</small></label><button class="button primary" data-action="confirm-job">确认已发表</button>${j.publication_sync?.supported ? '</details>' : ''}` : ""}
 ${!j.deleted_at && j.status === "uncertain" ? `<div class="notice spaced">系统不会自动重发结果不确定的请求。请先检查平台草稿箱和文章列表。</div><label class="check-label"><input type="checkbox" id="verified-not-received">我已在平台核对，确认未收到这篇文章</label><button class="button" data-action="not-received">登记为未接收</button>` : ""}
 ${!j.deleted_at && j.status === "published" ? `<hr class="section-divider"><h3>效果数据与复盘</h3><p>录入阅读、收藏和读者反馈，生成复盘并确认画像经验。</p><button class="button" data-action="performance-sample-open" data-id="${j.id}">录入数据 / 查看历史</button><a class="button" href="#analytics" data-action="close">打开数据复盘</a>` : ""}
 ${!j.deleted_at && j.channel_kind === "devto" && j.external_id && ["published", "draft"].includes(j.status) ? '<button class="button spaced" data-action="sync-metrics">↻ 同步 DEV 状态与数据</button>' : ""}
 ${!j.deleted_at && j.channel_kind === "wechat" && j.external_id && ["draft", "uncertain"].includes(j.status) ? `<div class="notice blue">${esc(j.note)}<br>公众号草稿编号：${esc(j.external_id)}</div><button class="button spaced" data-action="wechat-recheck">↻ 重新核对微信草稿</button>` : ""}
 <hr class="section-divider"><h3>执行记录</h3><div class="timeline">${j.events.map((e) => `<div class="timeline-item">${esc(e.message)}<small>${fmt(e.at)} · ${esc(statuses[e.status]?.[0] || e.status)}</small></div>`).join("") || '<span class="tip">没有额外执行记录</span>'}</div>`,
    `${!j.deleted_at && ["queued", "failed", "manual"].includes(j.status) ? '<button class="button danger" data-action="cancel-job">取消任务</button>' : ""}${!j.deleted_at && j.status === "failed" ? '<button class="button primary" data-action="retry-job">重新执行</button>' : ""}${jobRemovalButton(j)}<button class="button" data-action="close">关闭</button>`,
  );
}

async function uploadImage(cover) {
  const intendedPost = editor?.id;
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/png,image/jpeg,image/webp,image/gif";
  input.onchange = async () => {
    try {
      const file = input.files[0];
      if (!file) return;
      if (file.size > 5000000) throw new Error("图片大小不能超过 5 MB");
      const data = await fileToBase64(file),
        res = await api("/media", {
          data,
          title: file.name.slice(0, 150),
          collection:
            editor?.id === intendedPost ? editor.title.slice(0, 100) : "",
        });
      if (editor?.id !== intendedPost) return;
      if (cover) {
        $("#edit-cover").value = res.url;
      } else {
        const el = $("#edit-body");
        el.setRangeText(
          `\n![配图](${res.url})\n`,
          el.selectionStart,
          el.selectionEnd,
          "end",
        );
      }
      markDirty();
      preview();
      toast("图片已上传，请保存稿件");
    } catch (e) {
      toast(e.message);
    }
  };
  input.click();
}
const fileToBase64 = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.readAsDataURL(file);
  });
async function importMarkdown() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".md,.markdown,.txt";
  input.onchange = async () => {
    try {
      const file = input.files[0];
      if (!file) return;
      if (file.size > 800000) throw new Error("Markdown 文件不能超过 800 KB");
      let body = await file.text(),
        title = file.name.replace(/\.[^.]+$/, "");
      body = body.replace(/^\uFEFF/, "");
      if (body.trimStart().startsWith("---")) {
        modal(
          "导入 Markdown",
          `<div class="notice">文件包含 YAML 元数据。为避免误解析，请手动确认标题，并将需要的作者、摘要、封面填入编辑器独立字段。</div><label>标题<input id="import-title" value="${esc(title)}"></label><label>正文（请移除开头 YAML）<textarea id="import-body" rows="12">${esc(body)}</textarea></label>`,
          '<button class="button primary" data-action="finish-import">确认导入</button>',
        );
        return;
      }
      const heading = body.match(/^# (.+)\r?\n/);
      if (heading) {
        title = heading[1];
        body = body.slice(heading[0].length);
      }
      const p = await api("/posts", { title, body, stage: "draft" });
      await refresh(false);
      location.hash = "edit/" + p.id;
    } catch (e) {
      toast(e.message);
    }
  };
  input.click();
}

async function handleAction(button) {
  if (!['job-menu','platform-draft-menu'].includes(button.dataset.action)) closeJobMenu();
  if (modalBusy) {
    toast("请等待当前操作完成");
    return;
  }
  if (await handlePlatformAction(button)) return;
  if (await handleXhsDraftAction(button)) return;
  if (await handlePublishingBridgeAction(button)) return;
  if (await handleWorkflowAction(button)) return;
  if (await handleRepurposeAction(button)) return;
  if (await handleIllustrationAction(button)) return;
  if (await handleReviewAction(button)) return;
  if (await handlePerformanceAction(button)) return;
  if (await handleModelSettingsAction(button)) return;
  if (await handleXhsAction(button)) return;
  if (await handleAssetAction(button)) return;
  const action = button.dataset.action,
    id = button.dataset.id;
  if(action==='publication-recheck'){
    await api('/jobs/'+id+'/publication-recheck',{});
    await refresh(false);
    if(activeJob?.id===id && $('#modal').open)await showJob(id,true);
    toast('已安排核对平台发表结果，状态和链接会自动更新');return;
  }
  if(action==='register-publication'){
    if(dirty)throw new Error('请先保存当前稿件，再登记平台发布结果');
    const p=state.posts.find(p=>p.id===editor.id);
    const done=p.publication_progress?.published||[];
    modal('登记已公开发布',`<p>只补记平台发布结果，不会发送文章。已有链接会保留，无链接也可登记。</p><div class="channel-choices">${state.channels.filter(c=>c.id!=='local'&&(!p.target_channel||c.id===p.target_channel)).map(c=>`<label class="channel-choice"><input type="checkbox" data-published-channel="${esc(c.id)}" ${done.includes(c.id)?'checked disabled':''}>${esc(c.name)}${done.includes(c.id)?' · 已记录':''}</label>`).join('')}</div><p class="tip">确认所选平台已公开发表。仅在草稿箱的内容不能登记为已发布。</p>`,`<button class="button" data-action="close">取消</button><button class="button primary" data-action="register-publication-submit" data-id="${p.id}" data-version="${p.version}">确认所选平台已发布</button>`);
    return;
  }
  if(action==='register-publication-submit'){
    const channels=$$('[data-published-channel]:checked:not(:disabled)').map(el=>el.dataset.publishedChannel);
    if(!channels.length)throw new Error('请选择需要补记的平台');
    modalBusy=true;
    try{await api('/posts/'+id+'/register-publication',{version:Number(button.dataset.version),channels,confirmed:true});}
    finally{modalBusy=false;}
    closeModal();await refresh(false);syncEditorState();toast('已登记平台发布结果');return;
  }
  if (action === "job-menu" || action === 'platform-draft-menu') {
    showJobMenu(button, action === 'platform-draft-menu');
    return;
  }
  closeJobMenu();
  if(action==='platform-draft-publish') {
    await refresh(false);
    const p=state.posts.find(p=>p.id===id && !p.archived);
    if(!p)throw new Error('稿件已变化，请刷新后查看');
    if(platformDraftJobs(p).some(j=>j.status!=='canceled'))throw new Error('该平台已有发布记录，请从操作菜单查看和处理现有任务');
    await openPublish(p);return;
  }
  if(action==='platform-draft-register') {
    await refresh(false);
    const p=state.posts.find(p=>p.id===id && !p.archived);
    if(!p)throw new Error('稿件已变化，请刷新后查看');
    const name=state.channels.find(c=>c.id===p.target_channel)?.name || p.target_channel;
    modal('确认已公开发布',`<h3>${esc(p.title)}</h3><p>${esc(name)} · 请确认已在平台公开发表；仅存入草稿箱不算已发布。这里只登记结果，不会发送文章。</p><label>公开文章链接（可选）<input type="url" id="confirmation-url" placeholder="https://…"></label>`,`<button class="button" data-action="close">取消</button><button class="button primary" data-action="platform-draft-register-submit" data-id="${esc(p.id)}" data-version="${p.version}" data-platform="${esc(p.target_channel)}">确认已公开发布</button>`);return;
  }
  if(action==='platform-public-link') {
    const j=await api('/jobs/'+id);
    if(j.deleted_at || j.status!=='published')throw new Error('发布记录已变化，请刷新后查看');
    modal('文章链接',`<h3>${esc(j.snapshot.title)}</h3><p>${esc(j.channel_name)} · 填写公开文章地址，保存后可从首页直接查看。</p><label>公开文章链接<input id="confirmation-url" type="url" value="${esc(j.public_url || '')}" placeholder="https://…"></label>`,`<button class="button" data-action="close">取消</button><button class="button primary" data-action="platform-public-link-submit" data-id="${esc(id)}">保存链接</button>`);return;
  }
  if(['platform-draft-register-submit','platform-public-link-submit'].includes(action)) {
    const url=$('#confirmation-url').value.trim();
    modalBusy=true;
    try {
      if(action==='platform-public-link-submit')await api('/jobs/'+id+'/link',{url});
      else await api('/posts/'+id+'/register-publication',{version:Number(button.dataset.version),channels:[button.dataset.platform],confirmed:true,url});
    } finally {modalBusy=false;}
    closeModal();await refresh();toast(action==='platform-public-link-submit'?'文章链接已保存':'已登记为已发布');return;
  }
  if (action === "quick-confirm-job") {
    await showQuickConfirmation(id);
    return;
  }
  if (action === "quick-confirm-submit") {
    modalBusy = true;
    try {
      await api("/jobs/" + id + "/confirm", {url:$('#confirmation-url').value.trim()});
    } finally { modalBusy = false; }
    closeModal();
    await refresh();
    toast("已登记为已发布");
    return;
  }
  if (action.startsWith("profile-")) return handleProfileAction(action, button);
  if (action.startsWith("topic-")) return handleTopicAction(action, button);
  if (action === "close") {
    closeModal();
    return;
  }
  if (action === "new" || action === "new-idea") {
    if (profileDirty) throw new Error("请先保存画像，再新建内容");
    if (dirty && editor) {
      await saveEditor(true);
      if (dirty) throw new Error("请先保存当前稿件，再新建内容");
    }
    newPostDialog(action === "new-idea");
    return;
  }
  if (action === "edit") {
    location.hash = "edit/" + id;
    return;
  }
  if (action === "filter") {
    queueSelection.clear();
    filter = button.dataset.value;
    draw();
    return;
  }
  if (action === "create") {
    const idea = button.dataset.idea === "true",
      p = await api("/posts", {
        title: $("#new-title").value,
        body: idea ? $("#new-summary").value : "",
        summary: idea ? "" : $("#new-summary").value,
        tags: $("#new-tags")
          .value.split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
        source: $("#new-source").value,
        profile_id: $("#new-profile").value || null,
        stage: idea ? "idea" : "draft",
      });
    closeModal();
    await refresh(false);
    if (idea) {
      location.hash = "ideas";
      draw();
      toast("选题已记录");
    } else location.hash = "edit/" + p.id;
    return;
  }
  if (action === "start-writing") {
    const p = state.posts.find((p) => p.id === id);
    await api("/posts/" + id, { version: p.version, stage: "draft" });
    await refresh(false);
    location.hash = "edit/" + id;
    return;
  }
  if (action === "archive") {
    modal(
      "归档内容",
      `<p>归档「${esc(state.posts.find((p) => p.id === id).title)}」后，将从日常列表移出。文章、版本和发布记录仍然保留，可以随时恢复。</p>`,
      `<button class="button" data-action="close">取消</button><button class="button primary" data-action="confirm-archive" data-id="${id}">确认归档</button>`,
    );
    return;
  }
  if (action === "confirm-archive" || action === "restore") {
    await api("/posts/" + id + "/archive", {
      archived: action === "confirm-archive",
    });
    closeModal();
    await refresh();
    toast(action === "restore" ? "内容已恢复" : "内容已归档");
    return;
  }
  if (action === "save") {
    await saveEditor();
    return;
  }
  if (action === "wechat-draft" || action === "wechat-draft-cancel") {
    await requestWechatDraft(action === "wechat-draft-cancel");
    return;
  }
  if (action === "flow-metadata") {
    const field = !$("#edit-summary").value.trim() ? $("#edit-summary") : $("#edit-author");
    field.scrollIntoView({block:"center",behavior:"smooth"}); field.focus();
    return;
  }
  if (action === "publish") {
    await openPublish();
    return;
  }
  if (action === "check") {
    await runCheck();
    return;
  }
  if (action === "enqueue") {
    if (!report?.ok || selectedChannels().join(",") !== reportSelection)
      throw new Error("渠道已变化，请重新运行发布检查");
    const due =
      $("#publish-timing").value === "later" ? $("#publish-due").value : "";
    if ($("#publish-timing").value === "later" && !due)
      throw new Error("请选择发布时间");
    await api("/posts/" + publishPost.id + "/publish", {
      version: publishPost.version,
      channels: selectedChannels(),
      ack_warnings: $("#ack-warnings").checked,
      due_at: due ? new Date(due).toISOString() : null,
    });
    closeModal();
    await refresh(false);
    location.hash = "queue";
    toast("发布任务已创建，执行结果会自动更新");
    return;
  }
  if (action === "job") {
    await showJob(id);
    return;
  }
  if (action === "refresh-status") {
    await refresh(false);
    syncEditorState();
    if (!['edit','profile','settings','assets','library','chat','workflows','repurpose','illustrations','reviews','topics','analytics'].includes(view) && !$('#modal').open) draw();
    toast("状态已更新，当前输入已保留");
    return;
  }
  if (action === "refresh") {
    await refresh();
    toast("状态已刷新");
    return;
  }
  if (action === "clear-queue-selection") {
    queueSelection.clear();
    drawQueue();
    return;
  }
  if (
    [
      "batch-delete-jobs",
      "batch-restore-jobs",
      "batch-purge-jobs",
      "purge-job",
      "empty-trash",
    ].includes(action)
  ) {
    const batchAction =
      action === "batch-delete-jobs"
        ? "delete"
        : action === "batch-restore-jobs"
          ? "restore"
          : "purge";
    const ids =
      action === "purge-job"
        ? [id]
        : action === "empty-trash"
          ? state.jobs.filter((j) => j.deleted_at).map((j) => j.id)
          : [...queueSelection];
    await showQueueBatch(batchAction, ids, action === "empty-trash");
    return;
  }
  if (action === "confirm-queue-batch") {
    const batch = queueBatch;
    if (!batch) throw new Error("请重新选择记录并确认操作");
    if (batch.action === "purge" && !$("#purge-ack")?.checked)
      throw new Error("请先确认永久删除的范围");
    let result;
    modalBusy = true;
    const previous = button.textContent;
    button.textContent = "正在处理…";
    try {
      result = await api("/jobs/batch", {
        action: batch.action,
        ids: batch.ids,
        ...(batch.action === "purge" ? { confirmed: true } : {}),
      });
    } finally {
      modalBusy = false;
      if (button.isConnected) button.textContent = previous;
    }
    queueSelection.clear();
    closeModal();
    await refresh();
    toast(
      `${result.count} 条记录${{ delete: "已移到回收站", restore: "已恢复；已取消的排期不会自动执行", purge: "已彻底删除" }[batch.action]}`,
    );
    return;
  }
  if (action === "delete-job") {
    const j = await api("/jobs/" + id);
    if (j.deleted_at) {
      await refresh();
      toast("这条记录已经在回收站中");
      return;
    }
    if (j.status === "running") throw new Error("任务正在执行，完成后才能删除");
    modal(
      "删除发布记录",
      `<h3>${esc(j.snapshot.title)}</h3><p>${esc(j.channel_name)} · ${esc(statuses[j.status]?.[0] || j.status)}</p><p>记录将移到回收站，可以随时恢复。原稿、平台上已发布的文章和已有阅读统计会保留。</p>${j.status === "queued" ? '<div class="notice">这条任务尚未执行，删除时将同时取消排期。恢复记录后不会自动发布，需要重新创建发布任务。</div>' : ["manual", "draft", "uncertain"].includes(j.status) ? '<div class="notice">这条记录还有待处理结果。删除只收起记录，恢复后可继续核对平台。</div>' : ""}`,
      `<button class="button" data-action="close">保留记录</button><button class="button danger" data-action="confirm-delete-job" data-id="${j.id}">移到回收站</button>`,
    );
    return;
  }
  if (action === "confirm-delete-job" || action === "restore-job") {
    const restoring = action === "restore-job";
    await api("/jobs/" + id + (restoring ? "/restore" : "/delete"), {});
    closeModal();
    await refresh();
    toast(
      restoring
        ? "记录已恢复；已取消的排期不会自动执行"
        : "记录已移到回收站，可随时恢复",
    );
    return;
  }
  if (action === "cancel-job") {
    const jobId = activeJob.id;
    modal(
      "取消发布任务",
      "<p>取消后将不再继续执行。已经下载的发布包和文章内容会保留。</p>",
      `<button class="button" data-action="job" data-id="${jobId}">返回</button><button class="button danger" data-action="confirm-cancel" data-id="${jobId}">确认取消</button>`,
    );
    return;
  }
  if (action === "confirm-cancel") {
    await api("/jobs/" + id + "/cancel", {});
    closeModal();
    await refresh();
    toast("任务已取消");
    return;
  }
  if (
    action === "retry-job" ||
    action === "confirm-job" ||
    action === "not-received"
  ) {
    const j = activeJob,
      a = {
        "retry-job": "retry",
        "confirm-job": "confirm",
        "not-received": "not_received",
      }[action],
      values =
        a === "confirm"
          ? { url: $("#job-url").value }
          : a === "not_received"
            ? { verified: $("#verified-not-received").checked }
            : {};
    await api("/jobs/" + j.id + "/" + a, values);
    await refresh(false);
    await showJob(j.id);
    return;
  }
  if (action === "save-metrics") {
    const j = activeJob;
    const vals = { note: $("#metric-note").value };
    for (const k of ["reads", "likes", "comments"]) {
      const raw = $("#metric-" + k).value;
      if (raw === "") throw new Error("请填写完整的效果数据");
      vals[k] = Number(raw);
    }
    await api("/jobs/" + j.id + "/metrics", vals);
    await refresh(false);
    toast("效果数据与复盘笔记已保存");
    await showJob(j.id);
    return;
  }
  if (action === "wechat-recheck") {
    const j = activeJob;
    await api("/jobs/" + j.id + "/recheck", {});
    await refresh(false);
    await showJob(j.id);
    return;
  }
  if (action === "sync-metrics") {
    const j = activeJob;
    await api("/jobs/" + j.id + "/sync", {});
    await refresh(false);
    await showJob(j.id);
    toast("平台数据已同步");
    return;
  }
  if (action === "copy-md") {
    await navigator.clipboard.writeText(activeJob.snapshot.body);
    toast("Markdown 正文已复制");
    return;
  }
  if (action === "copy-html") {
    const j = activeJob,
      result = await api("/jobs/" + j.id + "/html");
    if (!navigator.clipboard.write || !window.ClipboardItem)
      throw new Error(
        "当前浏览器不支持复制排版，请下载发布包中的 article.html",
      );
    const content = `<section>${result.html}</section>`;
    await navigator.clipboard.write([
      new ClipboardItem({
        "text/html": new Blob([content], { type: "text/html" }),
        "text/plain": new Blob([j.snapshot.body], { type: "text/plain" }),
      }),
    ]);
    toast("排版内容已复制，请到平台编辑器粘贴");
    return;
  }
  if (action === "upload-body" || action === "upload-cover") {
    await uploadImage(action === "upload-cover");
    return;
  }
  if (action === "template") {
    const el = $("#edit-body");
    el.setRangeText(
      "\n\n## 为什么关注这个问题\n\n[待填写] 读者遇到了什么问题？\n\n## 我的实践与发现\n\n[待填写] 用具体步骤、事实或例子展开。\n\n## 可以直接用的建议\n\n- [待填写] 第一个可执行的建议\n- [待填写] 第二个可执行的建议\n\n## 参考资料\n\n[待填写] 补充可靠来源。\n",
      el.selectionStart,
      el.selectionEnd,
      "end",
    );
    markDirty();
    preview();
    return;
  }
  if (action === "history") {
    const result = await api("/posts/" + editor.id + "/revisions");
    modal(
      "稿件版本历史",
      `<p class="tip">恢复会生成一个新版本，已排期任务仍使用原快照。显示最近 30 个版本。</p>${result.revisions
        .map((r) => {
          const p = JSON.parse(r.snapshot);
          return `<div class="content-row"><div class="row-main"><h3>v${r.version} · ${esc(p.title)}</h3><small class="muted">${fmt(r.created_at)} · ${p.body.length} 字</small></div><button class="button small" data-action="restore-version" data-version="${r.version}">采用此版本</button></div>`;
        })
        .join("")}`,
    );
    return;
  }
  if (action === "restore-version") {
    if (dirty) throw new Error("请先保存当前修改，再恢复历史版本");
    const versions = await api("/posts/" + editor.id + "/revisions"),
      p = JSON.parse(
        versions.revisions.find(
          (r) => r.version === Number(button.dataset.version),
        ).snapshot,
      ),
      saved = await api("/posts/" + editor.id, {
        ...p,
        version: editor.version,
      });
    editor = saved;
    state.posts = state.posts.map((p) => (p.id === saved.id ? saved : p));
    dirty = false;
    closeModal();
    drawEditor();
    toast("历史内容已保存为新版本");
    return;
  }
  if (action === "import-md") {
    await importMarkdown();
    return;
  }
  if (action === "finish-import") {
    const p = await api("/posts", {
      title: $("#import-title").value,
      body: $("#import-body").value,
      stage: "draft",
    });
    closeModal();
    await refresh(false);
    location.hash = "edit/" + p.id;
    return;
  }
  if (action === "program-preset") {
    const preset = JSON.parse(button.dataset.preset);
    $("#ai-program").value = preset.program;
    $("#ai-program-args").value = JSON.stringify(preset.args, null, 2);
    $("#ai-program-output").value = preset.output;
    toast("已填入预设，请保存并测试登录状态");
    return;
  }
  if (action === "fetch-ai-models") {
    const baseInput = $("#ai-base"), keyInput = $("#ai-key");
    const base = baseInput.value, key = keyInput.value;
    const target = $("#ai-model-list-result"), list = $("#ai-model-list");
    button.disabled = true;
    target.textContent = "正在获取模型列表…";
    resetModelChoices("正在获取模型列表…");
    try {
      const result = await api("/ai/models", {base_url: base, api_key: key});
      if (!target.isConnected || baseInput.value !== base || keyInput.value !== key) return;
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = result.models.length ? "请选择模型" : "接口未返回模型";
      list.replaceChildren(placeholder, ...result.models.map((model) => {
        const option = document.createElement("option");
        option.value = model;
        option.textContent = model;
        return option;
      }));
      list.disabled = !result.models.length;
      syncModelChoice();
      target.textContent = result.models.length
        ? `已获取 ${result.models.length} 个模型，从下拉框选择或手动填写模型名称；选择后请保存并测试。`
        : "接口未返回模型，仍可手动填写模型名称。";
    } catch (error) {
      if (target.isConnected && baseInput.value === base && keyInput.value === key) {
        resetModelChoices("获取失败，请重新获取");
        target.textContent = error.message + "；仍可手动填写模型名称。";
      }
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (action === "save-settings" || action === "test-ai" || action === "test-wechat") {
    const guideSavedFields = captureUsageSettings();
    await saveModelSettings();
    await api("/settings", {
      ...wechatSettingsValues(),
      AI_MODE: $("#ai-mode").value,
      AI_PROGRAM: $("#ai-program").value,
      AI_PROGRAM_ARGS: $("#ai-program-args").value,
      AI_PROGRAM_OUTPUT: $("#ai-program-output").value,
      AI_PROGRAM_TIMEOUT: $("#ai-program-timeout").value,
      DEVTO_API_KEY: $("#devto-key").value,
      AI_BASE_URL: $("#ai-base").value,
      AI_MODEL: $("#ai-model").value,
      AI_API_KEY: $("#ai-key").value,
      channels: (await api('/platforms')).platforms.filter(c=>['wechat','devto'].includes(c.id)).map((c) => ({
        id: c.id,
        enabled: !!c.enabled,
        ...(c.id === "wechat" ? {mode: $("#wechat-mode").value} : {}),
        ...(c.id === "devto"
          ? { publish: $("#devto-publish").value === "true" }
          : {}),
      })),
    });
    rememberUsageSettings(guideSavedFields);
    if (action === "test-wechat") {
      const target = $("#wechat-test-result");
      target.textContent = "正在连接公众号并检查草稿权限…";
      try {
        const result = await api("/wechat/test", {});
        target.textContent = result.note || result.message || "连接成功：可以读取公众号草稿箱";
      } catch (error) { target.textContent = "连接失败：" + error.message; }
      state = await api("/state");
      return;
    }
    if (action === "test-ai") {
      const target = $("#ai-test-result");
      target.textContent = "正在测试，请等待程序返回（最长为设置的超时时间）…";
      try {
        const result = await api("/ai/test", {});
        target.textContent = "调用成功：" + result.body.slice(0, 500);
      } catch (error) {
        target.textContent = "测试失败：" + error.message;
      }
      state = await api("/state");
      return;
    }
    await refresh();
    toast("设置已保存；已有任务继续使用原发布方式");
    return;
  }
  if (action === "backup") {
    token = (await api("/session")).token;
    const r = await fetch("/api/backup", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Studio-Token": token },
      body: "{}",
    });
    if (!r.ok) throw new Error("备份失败，请检查数据目录");
    const blob = await r.blob(),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download =
      "paperplane-backup-" + new Date().toISOString().slice(0, 10) + ".zip";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast("完整备份已生成，数据库副本也保存在本地");
    return;
  }
  if (action === "migrate") {
    const r = await api("/legacy/import", {});
    await refresh(false);
    toast(`导入完成：${r.posts} 篇内容、${r.jobs} 条记录；已有内容已跳过`);
    return;
  }
  if (action === "ai" || action === "generate") {
    await openWorkflowDialog(editor?.id);
    return;
  }

  if (action === "use-generated") {
    const body = $("#generated-body").value;
    $("#edit-body").value = body;
    closeModal();
    markDirty();
    preview();
    toast("已放入编辑器，请检查后保存");
    return;
  }
}

document.addEventListener("click", async (e) => {
  const button = e.target.closest("[data-action]");
  if (!button || button.disabled) return;
  e.preventDefault();
  button.disabled = true;
  try {
    await handleAction(button);
  } catch (error) {
    if ($("#modal").open) modalError(error);
    else toast(error.message);
  } finally {
    if (button.isConnected) button.disabled = false;
  }
});
document.addEventListener("input", (e) => {
  if (e.target.closest(".editor-grid")) {
    markDirty();
    if (e.target.id === "edit-body") {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(preview, 220);
    }
  }
  if (e.target.id === "search") {
    queueSelection.clear();
    query = e.target.value;
    const pos = e.target.selectionStart;
    draw();
    const input = $("#search");
    input?.focus();
    if (input && input.type !== "search") input.setSelectionRange(pos, pos);
  }
});
document.addEventListener("change", (e) => {
  if (e.target.id === "purge-ack") {
    $("#confirm-queue-batch").disabled = !e.target.checked;
  }
  if (e.target.id === "queue-select-all" || e.target.dataset.queueSelect) {
    const candidates = visibleQueueJobs().filter((j) => j.status !== "running");
    if (e.target.id === "queue-select-all") {
      if (e.target.checked && candidates.length > queueBatchLimit) {
        e.target.checked = false;
        toast(`每次最多选择 ${queueBatchLimit} 条，请缩小筛选范围`);
        return;
      }
      queueSelection.clear();
      if (e.target.checked) candidates.forEach((j) => queueSelection.add(j.id));
    } else {
      const id = e.target.dataset.queueSelect;
      if (e.target.checked && candidates.some((j) => j.id === id)) {
        if (queueSelection.size >= queueBatchLimit) {
          e.target.checked = false;
          toast(`每次最多选择 ${queueBatchLimit} 条`);
          return;
        }
        queueSelection.add(id);
      } else queueSelection.delete(id);
    }
    drawQueue();
  }
  if (e.target.id === "publish-timing")
    $("#schedule-field").classList.toggle("hidden", e.target.value !== "later");
  if (e.target.name === "publish-channel") {
    report = null;
    $("#enqueue-button").disabled = true;
    $("#publish-checks").innerHTML =
      '<p class="tip">渠道已变化，请重新运行发布检查。</p>';
  }
});
document.addEventListener("keydown", async (e) => {
  const menu = $("#job-quick-menu");
  if (menu?.matches(":popover-open")) {
    if (e.key === "Escape") {
      e.preventDefault();
      closeJobMenu(true);
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const items = $$("button:not(:disabled)", menu), index = items.indexOf(document.activeElement);
      const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next].focus();
      return;
    }
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && editor) {
    e.preventDefault();
    try {
      await saveEditor();
    } catch (err) {
      toast(err.message);
    }
  }
});
document.addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="#"]');
  if (a && profileDirty && a.hash !== location.hash) {
    e.preventDefault();
    toast("请先保存画像，或通过“返回画像列表”放弃修改");
    return;
  }
  if (a && dirty && a.hash !== location.hash) {
    e.preventDefault();
    const dest = a.hash;
    modal(
      "稿件还没有保存",
      "<p>保存后再离开，避免丢失刚才的修改。</p>",
      `<button class="button" data-action="close">继续编辑</button><button class="button primary" id="save-and-leave">保存并离开</button>`,
    );
    $("#save-and-leave").onclick = async () => {
      try {
        await saveEditor(true);
        if (dirty) throw new Error("仍有未保存修改，请重试");
        closeModal();
        location.hash = dest;
      } catch (err) {
        modalError(err);
      }
    };
  }
});
window.addEventListener("beforeunload", (e) => {
  if (dirty || profileDirty || sharedLibraryUploading || repurposeDirty || illustrationDirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.PaperPlaneDesktop = Object.freeze({
  hasUnsaved: () => dirty || profileDirty || sharedLibraryUploading || repurposeDirty || illustrationDirty,
  snapshot: () => dirty && editor ? {
    ...editorValues(),
    post_id: editor.id,
    saved_version: editor.version,
  } : null,
});
let previousHash = location.hash;
window.addEventListener("hashchange", () => {
  if (illustrationDirty && location.hash !== previousHash) {
    history.replaceState(null, "", previousHash);
    toast('请先保存配图计划或图片设置，再切换页面');
    return;
  }
  if (repurposeDirty && location.hash !== previousHash) {
    history.replaceState(null, "", previousHash);
    toast("请先保存平台稿或放弃修改，再切换页面");
    return;
  }
  if (sharedLibraryUploading && location.hash !== previousHash) {
    history.replaceState(null, "", previousHash);
    toast("资料正在导入，请完成后再切换页面");
    return;
  }
  if (profileDirty && location.hash !== previousHash) {
    history.replaceState(null, "", previousHash);
    toast("请先保存画像，或通过“返回画像列表”放弃修改");
    return;
  }
  if (dirty && view === "edit" && location.hash !== previousHash) {
    const wanted = location.hash;
    history.replaceState(null, "", previousHash || "#home");
    toast("请先保存当前稿件，再切换页面");
    return;
  }
  closeModal();
  previousHash = location.hash;
  queueSelection.clear();
  filter = "all";
  query = "";
  draw();
  void syncVisibleState();
  window.scrollTo(0, 0);
});
$("#modal").addEventListener("cancel", (e) => {
  if (modalBusy || repurposeDirty) e.preventDefault();
});
$("#modal").addEventListener("close", () => {
  queuePreparation++;
  queueBatch = null;
  if (!modalBusy && !dirty && view !== "edit" && view !== "settings") draw();
});
$("#local-date").textContent = new Date().toLocaleDateString("zh-CN", {
  month: "long",
  day: "numeric",
  weekday: "long",
});
(async () => {
  try {
    const session = await api("/session");
    token = session.token;
    deployment = session.deployment || "local";
    await refresh();
    if (!usageGuide) await loadUsageGuide();
    void checkModelSetup(true);
  } catch (e) {
    $("#main").innerHTML = empty(
      "暂时无法连接工作台",
      esc(e.message),
      '<button class="button" data-action="refresh">重试连接</button>',
    );
    $("#service-state").innerHTML =
      '<span class="status-dot offline"></span>工作台未连接';
  }
})();
function syncEditorState(){
  if(view!=='edit'||!editor||!$('#edit-title'))return;
  const latest=state.posts.find(p=>p.id===editor.id);if(!latest)return;
  $('#editor-stage').innerHTML=publicationBadge(latest);
  if(latest.version!==editor.version){
    if(dirty||document.activeElement?.matches('input,textarea,select')||$('#modal').open){
      $('#save-state').textContent='其他页面已有更新；当前输入已保留，请核对后保存';
    }else{editor=latest;drawEditor();}
  }else editor={...editor,publication_progress:latest.publication_progress};
}
async function syncVisibleState(){
  if(pollBusy||document.hidden||modalBusy)return;
  pollBusy=true;
  try{
    const route=location.hash, startedView=view;
    // A response may arrive after navigation or after the user starts editing.
    const safe=()=>location.hash===route&&view===startedView&&!document.hidden&&!modalBusy
      &&!['edit','profile','settings','assets','library','chat','workflows','repurpose','illustrations','reviews','topics','analytics'].includes(view)
      &&!$('dialog[open]')&&!document.activeElement?.matches('input,textarea,select')&&!document.activeElement?.isContentEditable;
    await refresh(safe);syncEditorState();await refreshArticleFlow();
  }catch{$('#service-state').innerHTML='<span class="status-dot offline"></span>连接中断，正在重连';}
  finally{pollBusy=false;}
}
setInterval(syncVisibleState,3000);
window.addEventListener('focus',syncVisibleState);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void syncVisibleState();});
document.addEventListener('focusout',()=>setTimeout(syncVisibleState,0));
if(stateUpdates)stateUpdates.onmessage=()=>void syncVisibleState();

function resetModelChoices(message) {
  const list = $("#ai-model-list");
  if (!list) return;
  const option = document.createElement("option");
  option.value = "";
  option.textContent = message;
  list.replaceChildren(option);
  list.disabled = true;
}

function syncModelChoice() {
  const list = $("#ai-model-list"), input = $("#ai-model");
  if (list && input) list.value = Array.from(list.options).some(option => option.value === input.value) ? input.value : "";
}

document.addEventListener("change", (event) => {
  if (event.target.id === "ai-model-list" && event.target.value) {
    $("#ai-model").value = event.target.value;
  }
  if (event.target.id === "ai-mode") {
    $("#ai-program-fields").hidden = event.target.value !== "program";
    $("#ai-api-fields").hidden = event.target.value === "program";
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "ai-model") syncModelChoice();
  if (["ai-base", "ai-key"].includes(event.target.id)) {
    resetModelChoices("接口或密钥已调整，请重新获取");
    const target = $("#ai-model-list-result");
    if (target) target.textContent = "接口或密钥已调整，请重新获取模型列表；仍可手动填写。";
  }
});

// Show the running server version, including in the installed desktop webview.
api("/health").then(info => {
  const badge = document.querySelector("#app-version");
  if (badge && /^\d+\.\d+\.\d+$/.test(info.version)) {
    badge.textContent = "v" + info.version;
    badge.hidden = false;
  }
}).catch(() => {});

function publishingBridgePanel(b) {
  if (deployment === 'cloud') return '';
  if (b.desktop) return `<section class="panel spaced"><div class="panel-head"><h2>平台账号与图文草稿</h2></div><div class="panel-body"><p>在纸飞机内登录平台，采用平台稿后自动上传配图并送入草稿箱。CSDN、掘金和知乎在平台窗口确认发表后，纸飞机自动核对状态和文章链接。博客园、开源中国、思否、51CTO、头条号、B站专栏、简书和微博头条文章支持图文草稿；公开发表后请从任务菜单登记结果。各渠道的实际验通状态可在渠道管理查看。登录会保存在本机，关闭窗口、重启和升级后继续复用；平台登录过期时才需重新登录。</p>${['csdn','juejin','zhihu','cnblogs','oschina','segmentfault','51cto','toutiao','bilibili-article','jianshu','weibo'].map(id=>`<div class="setting-channel"><div class="channel-info"><b>${({csdn:'CSDN',juejin:'掘金',zhihu:'知乎',cnblogs:'博客园',oschina:'开源中国',segmentfault:'思否','51cto':'51CTO',toutiao:'头条号','bilibili-article':'B站专栏',jianshu:'简书',weibo:'微博'})[id]}</b><small>${b.accounts[id]?(b.account_states?.[id]==='verified'?'已登录 · ':'已记住账号，打开时核验 · ')+esc(b.accounts[id].name):'请打开平台核验已有登录'}</small></div><button class="button" data-action="publishing-open" data-platform="${id}" ${b.busy?'disabled':''}>打开账号与草稿</button></div>`).join('')}<div id="publishing-result" role="status">${esc(b.error || b.progress || '')}</div><button class="button small spaced" data-action="publishing-refresh">刷新登录状态</button></div></section>`;
  return `<section class="panel spaced"><div class="panel-head"><h2>CSDN · 掘金自动送草稿</h2></div><div class="panel-body"><p>${b.connected ? '浏览器扩展已连接' : b.enabled ? '等待浏览器扩展连接' : '尚未启用'}。采用平台稿后，由纸飞机上传图片并送入平台草稿箱，公开发表仍需在平台完成。</p><p class="tip">首次连接：安装文章同步助手，在扩展的 CLI / MCP 设置中启用连接，填入下方地址与令牌，并在同一浏览器登录 CSDN、掘金。日常使用需保持浏览器运行。</p><a class="button" href="https://chromewebstore.google.com/detail/文章同步助手/hchobocdmclopcbnibdnoafilagadion" target="_blank" rel="noopener">打开官方扩展安装页</a> <button class="button" data-action="publishing-setup">${b.enabled ? '查看连接配置' : '启用自动送草稿'}</button> <button class="button" data-action="publishing-check">检查平台登录</button><div id="publishing-result" role="status">${esc(b.error || '')}</div></div></section>`;
}
async function handlePublishingBridgeAction(button) {
  const action=button.dataset.action;
  if (action==='publishing-open') {
    button.disabled=true;
    try {
      await api('/publishing-bridge/open',{platform:button.dataset.platform,job_id:button.dataset.id || null});
      toast('已打开纸飞机平台窗口，请完成登录或核对草稿');
      if(view==='settings') await drawSettings();
      if(view==='platforms') await refresh();
    } finally {button.disabled=false;}
    return true;
  }
  if (action==='publishing-refresh') {await drawSettings();return true;}
  if (!['publishing-setup','publishing-check'].includes(action)) return false;
  button.disabled=true;
  try {
    const r=await api('/publishing-bridge/'+(action==='publishing-setup'?'setup':'check'),{});
    const target=$('#publishing-result');
    if (action==='publishing-setup') {
      target.innerHTML=`<label>本机连接地址<input readonly value="${esc(r.address)}"></label><label>连接令牌（复制到扩展设置，不要分享）<input type="password" readonly id="publishing-token" value="${esc(r.token)}"></label><p>${esc(r.error || '点击令牌框后按 Ctrl+A、Ctrl+C 复制；设置扩展后点击检查平台登录。')}</p>`;
      $('#publishing-token').onclick=e=>e.target.select();
    } else target.innerHTML=r.accounts.length ? r.accounts.map(a=>`<p>${esc(a.name || a.id)}：${a.authenticated?'已登录 '+esc(a.username):'未登录，请先登录平台'}</p>`).join('') : '<p>未检测到 CSDN / 掘金账号。</p>';
  } finally {button.disabled=false;}
  return true;
}
