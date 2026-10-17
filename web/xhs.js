"use strict";
let xhsPost = null,
  xhsPhotos = [],
  xhsAssets = [],
  xhsCards = [];
function xhsPlain(body) {
  return body
    .replace(/^\*AI 生成流程示意图，非实际操作截图。\*\s*$/gm, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*`]/g, "")
    .trim();
}
function drawXhsPhotos() {
  $("#xhs-count").textContent =
    `已选 ${xhsPhotos.length} / 18 张 · 第一张为封面`;
  $("#xhs-photos").innerHTML = xhsPhotos
    .map(
      (url, i) =>
        `<article class="asset-card"><img src="${esc(url)}" alt="第 ${i + 1} 张图片"><div><b>${i === 0 ? "封面" : `第 ${i + 1} 张`}</b><div class="actions"><button class="button small" data-action="xhs-up" data-index="${i}" ${i === 0 ? "disabled" : ""}>前移</button><button class="button small" data-action="xhs-down" data-index="${i}" ${i === xhsPhotos.length - 1 ? "disabled" : ""}>后移</button><button class="button small" data-action="xhs-remove" data-index="${i}">移除</button></div></div></article>`,
    )
    .join("");
  const q = $("#xhs-search").value.toLowerCase();
  $("#xhs-library").innerHTML = xhsAssets
    .filter(
      (a) =>
        !a.archived &&
        !xhsPhotos.includes(a.url) &&
        `${a.title} ${a.collection} ${a.description}`.toLowerCase().includes(q),
    )
    .map(
      (a) =>
        `<article class="asset-card"><img src="${esc(a.url)}" alt="${esc(a.title)}" loading="lazy"><div><b>${esc(a.title)}</b><small>${esc(a.collection || "未分类")}</small><button class="button small" data-action="xhs-add" data-id="${a.id}">加入图集</button></div></article>`,
    )
    .join("");
}
function makeXhsCard(title, text, index, total, planned = false) {
  const canvas = document.createElement("canvas");
  canvas.width = 1080;
  canvas.height = 1440;
  const c = canvas.getContext("2d");
  c.fillStyle = index === 0 ? "#172f49" : "#f8f5ee";
  c.fillRect(0, 0, 1080, 1440);
  function wrap(s, y, size, color) {
    c.font = `${size <= 34 ? 400 : 600} ${size}px "Microsoft YaHei", sans-serif`;
    c.fillStyle = color;
    let line = "";
    for (const ch of s) {
      if (ch === "\n" || c.measureText(line + ch).width > 880) {
        c.fillText(line, 100, y);
        y += size * 1.5;
        line = ch === "\n" ? "" : ch;
      } else line += ch;
    }
    if (line) {
      c.fillText(line, 100, y);
      y += size * 1.5;
    }
    return y;
  }
  c.fillStyle = "#de713f";
  c.fillRect(100, 110, 90, 10);
  const titleEnd = wrap(
    title,
    index === 0 && !planned ? 390 : 220,
    index === 0 && !planned ? 64 : 44,
    index === 0 ? "#ffffff" : "#172f49",
  );
  if (index === 0 && !planned)
    wrap("图文笔记 · 向后翻阅", Math.max(850, titleEnd + 80), 32, "#f5cbb2");
  else wrap(text, Math.max(390, titleEnd + 45), 34, index === 0 ? "#ffffff" : "#334155");
  c.font = '28px "Microsoft YaHei", sans-serif';
  c.fillStyle = index === 0 ? "#d7e3ef" : "#64748b";
  c.fillText(`正文排版图卡 · ${index + 1} / ${total}`, 100, 1350);
  return canvas.toDataURL("image/png");
}
async function handleXhsAction(button) {
  const action = button.dataset.action;
  if (action === "xhs-open") {
    await saveEditor(true);
    if (dirty) throw new Error("请先保存当前文章");
    xhsPost = { id: editor.id, version: editor.version };
    xhsCards = [];
    xhsAssets = (await api("/assets")).assets;
    const note = editor.xhs || {};
    xhsPhotos = [...(note.photos || [])];
    modal(
      "小红书图文",
      `<p class="tip">单独整理标题、短文案和图片，不会改写原文章。先保存图文，再把文章设为待发布并选择小红书渠道。</p><label>小红书标题<input id="xhs-title" maxlength="20" value="${esc(note.title || editor.title)}"><small>必须为 1–20 字，标点、空格和数字也计入；母稿标题保留。</small></label><label>小红书文案<textarea id="xhs-caption" rows="5" maxlength="5000">${esc(note.caption || editor.summary)}</textarea><small>建议 1000 字以内；话题请写入文案并在平台核对。</small></label><h3 id="xhs-count"></h3><div id="xhs-photos" class="asset-grid"></div><hr><h3>正文转竖版图卡</h3><p class="tip">无需模型，把你确认的内容绘制为 1080 × 1440 PNG：一张封面加多张内容卡。不会伪装为过程截图。</p><label>图卡正文<textarea id="xhs-card-text" rows="5" maxlength="4000">${esc(xhsPlain(editor.body))}</textarea></label><button class="button" data-action="xhs-cards-preview">预览正文图卡</button><div id="xhs-cards-preview" class="asset-grid"></div><button class="button" data-action="xhs-cards-save" id="xhs-cards-save" hidden>确认图卡并加入图集</button><p id="xhs-card-status" role="status"></p><hr><h3>从过程素材选图</h3><label>搜索项目或图片<input id="xhs-search" placeholder="例如：网络排查"></label><div id="xhs-library" class="asset-grid"></div>`,
      '<button class="button" data-action="close">关闭</button><button class="button primary" data-action="xhs-save">保存小红书图文</button><button class="button" data-action="xhs-illustrations">保存并规划插画</button>',
    );
    if (note.pages?.length) {
      $('#xhs-card-text').closest('label').insertAdjacentHTML('beforebegin',
        `<section id="xhs-page-plan"><h3>独立分页计划</h3><p class="tip">可调整每页内容，再按此计划生成图卡。视觉安排用于后续插画制作，当前预览为文字排版图卡。</p>${note.pages.map((p, i) => `<div class="panel panel-body spaced"><b>第 ${i + 1} 页${i === 0 ? ' · 封面' : ''}</b><label>页标题<input id="xhs-page-title-${i}" maxlength="40" value="${esc(p.title)}"></label><label>页文案<textarea id="xhs-page-text-${i}" rows="3" maxlength="220">${esc(p.text)}</textarea></label><label>视觉安排<textarea id="xhs-page-visual-${i}" rows="2" maxlength="1000">${esc(p.visual)}</textarea></label></div>`).join('')}<button class="button" data-action="xhs-plan-preview">按分页计划预览图卡</button></section>`);
    }
    drawXhsPhotos();
    return true;
  }
  if (action === "xhs-add") {
    if (xhsPhotos.length >= 18)
      throw new Error("工作台图集最多 18 张，请先移除部分图片");
    const a = xhsAssets.find((a) => a.id === button.dataset.id);
    if (!xhsPhotos.includes(a.url)) xhsPhotos.push(a.url);
    drawXhsPhotos();
    return true;
  }
  if (["xhs-up", "xhs-down", "xhs-remove"].includes(action)) {
    const i = Number(button.dataset.index);
    if (action === "xhs-remove") xhsPhotos.splice(i, 1);
    else {
      const j = i + (action === "xhs-up" ? -1 : 1);
      if (j >= 0 && j < xhsPhotos.length)
        [xhsPhotos[i], xhsPhotos[j]] = [xhsPhotos[j], xhsPhotos[i]];
    }
    drawXhsPhotos();
    return true;
  }
  if (action === 'xhs-plan-preview') {
    const pages = xhsPageValues();
    if (xhsPhotos.length + pages.length > 18) throw new Error('图片总数超过18张，请先移除部分图片');
    if (pages.some(p => !p.title.trim() || !p.text.trim())) throw new Error('请填写各页标题和文案');
    xhsCards = pages.map((p, i) => ({title: p.title, data: makeXhsCard(p.title, p.text, i, pages.length, true)}));
    $('#xhs-cards-preview').innerHTML = xhsCards.map(c => `<img class="asset-edit-preview" src="${c.data}" alt="${esc(c.title)}">`).join('');
    $('#xhs-cards-save').hidden = false;
    return true;
  }
  if (action === "xhs-cards-preview") {
    const text = $("#xhs-card-text").value.trim(),
      title = $("#xhs-title").value.trim();
    if (!text || !title) throw new Error("请填写图卡正文和标题");
    if ([...title].length > 40) throw new Error("图卡标题请缩短至 40 字以内");
    const chunks = [];
    let page = "",
      rows = 0;
    for (const paragraph of text.split(/\n+/).filter((p) => p.trim())) {
      const chars = [...paragraph.trim()];
      while (chars.length) {
        const part = chars.splice(0, 220).join("");
        const used = Math.ceil([...part].length / 24) + 1;
        if (page && (rows + used > 13 || [...(page + part)].length > 220)) {
          chunks.push(page);
          page = "";
          rows = 0;
        }
        page += (page ? "\n\n" : "") + part;
        rows += used;
      }
    }
    if (page) chunks.push(page);
    if (chunks.length + 1 + xhsPhotos.length > 18)
      throw new Error("图集将超过 18 张，请精简图卡正文或移除已选图片");
    xhsCards = ["", ...chunks].map((t, i) => ({
      title: `${title} ${i === 0 ? "封面" : `图卡${i}`}`,
      data: makeXhsCard(title, t, i, chunks.length + 1),
    }));
    $("#xhs-cards-preview").innerHTML = xhsCards
      .map(
        (c) =>
          `<img class="asset-edit-preview" src="${c.data}" alt="${esc(c.title)}">`,
      )
      .join("");
    $("#xhs-cards-save").hidden = false;
    return true;
  }
  if (action === "xhs-cards-save") {
    if (xhsPhotos.length + xhsCards.length > 18)
      throw new Error("图片总数超过 18 张，请先移除部分图片");
    modalBusy = true;
    try {
      while (xhsCards.length) {
        const card = xhsCards[0];
        const saved = await api("/media", {
          data: card.data.split(",")[1],
          title: card.title,
          collection: `小红书：${$("#xhs-title").value}`.slice(0, 100),
          origin: "text-card",
          description: "由用户确认的正文排版生成",
        });
        xhsPhotos.push(saved.url);
        xhsCards.shift();
        $("#xhs-card-status").textContent =
          `已入库，当前图集 ${xhsPhotos.length} 张；还需保存小红书图文。`;
      }
      $("#xhs-cards-save").hidden = true;
      $("#xhs-cards-preview").innerHTML = "";
    } finally {
      modalBusy = false;
      drawXhsPhotos();
    }
    return true;
  }
  if (["xhs-save", "xhs-illustrations"].includes(action)) {
    if (editor?.id !== xhsPost.id) throw new Error("文章已变化，请重新打开");
    const saved = await api(`/posts/${xhsPost.id}`, {
      version: xhsPost.version,
      xhs: {
        title: $("#xhs-title").value,
        caption: $("#xhs-caption").value,
        photos: xhsPhotos,
        ...(editor.xhs?.pages?.length ? {pages: xhsPageValues()} : {}),
      },
    });
    editor = saved;
    state.posts = state.posts.map((p) => (p.id === saved.id ? saved : p));
    closeModal();
    drawEditor();
    toast("小红书图文已保存，可进入发布检查");
    if (action === 'xhs-illustrations') {
      const plan = await api('/illustrations', {post_id: editor.id, post_version: editor.version});
      location.hash = '#illustrations/' + plan.id;
    }
    return true;
  }
  if (action === "xhs-copy-title" || action === "xhs-copy-caption") {
    await navigator.clipboard.writeText(
      (activeJob.snapshot.xhs_delivery || activeJob.snapshot.xhs)[action === "xhs-copy-title" ? "title" : "caption"],
    );
    toast("已复制");
    return true;
  }
  return false;
}
function xhsPageValues() {
  return (editor.xhs?.pages || []).map((p, i) => ({title: $(`#xhs-page-title-${i}`).value,
    text: $(`#xhs-page-text-${i}`).value, visual: $(`#xhs-page-visual-${i}`).value}));
}
document.addEventListener("input", (e) => {
  if (e.target.id === "xhs-search") drawXhsPhotos();
});
