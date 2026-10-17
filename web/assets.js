"use strict";
let assetRows = [],
  assetCollection = "",
  assetQuery = "",
  assetArchived = false;
let assetPickerPost = null,
  diagramPost = null,
  diagramProposal = null;

function assetOptions() {
  return [...new Set(assetRows.map((a) => a.collection).filter(Boolean))]
    .sort()
    .map((c) => `<option value="${esc(c)}"></option>`)
    .join("");
}
function assetFields(a = {}) {
  return `<label>问题 / 项目<input id="asset-collection" list="asset-collections" maxlength="100" value="${esc(a.collection ?? (assetCollection === "__unfiled" ? "" : assetCollection))}" placeholder="例如：ESP32 无法联网排查"><datalist id="asset-collections">${assetOptions()}</datalist></label><label>图片名称<input id="asset-title" maxlength="150" value="${esc(a.title || "")}" placeholder="批量上传时留空，使用原文件名"></label><label>步骤编号<input id="asset-step" type="number" min="0" max="9999" value="${a.step || 0}"><small>0 表示不指定步骤；批量上传按选择顺序依次编号。</small></label><label>过程说明<textarea id="asset-description" rows="3" maxlength="2000" placeholder="当时做了什么、现象是什么、结论或待验证点">${esc(a.description || "")}</textarea></label>`;
}
function readAssetFields() {
  return {
    collection: $("#asset-collection").value,
    title: $("#asset-title").value,
    step: Number($("#asset-step").value),
    description: $("#asset-description").value,
  };
}
function diagramAssetDescription(proposal) {
  const provenance = proposal.model_provenance || {};
  const request = provenance.requested_model || "未记录";
  const returned = provenance.reported_model || "未提供";
  return `流程文字由文字模型提炼，请求模型：${request}；接口返回标识：${returned}。图片由本机确定性排版生成，未调用图片模型，非实际操作截图。`;
}
async function drawAssets() {
  $("#main").innerHTML =
    heading("图片素材", "按问题或项目保存过程图片，跨文章复用。") +
    "<p>正在载入…</p>";
  try {
    assetRows = (await api("/assets")).assets;
    if (view !== "assets") return;
    $("#main").innerHTML =
      heading(
        "图片素材",
        "真实截图保留过程，AI 示意图单独标明来源。",
        '<button class="button primary" data-action="asset-upload">＋ 批量归档图片</button>',
      ) +
      `<section class="panel panel-body"><div class="form-grid"><label>问题 / 项目<select id="asset-filter"><option value="">全部项目</option><option value="__unfiled">未分类</option>${[
        ...new Set(assetRows.map((a) => a.collection).filter(Boolean)),
      ]
        .sort()
        .map(
          (c) =>
            `<option value="${esc(c)}" ${assetCollection === c ? "selected" : ""}>${esc(c)}</option>`,
        )
        .join(
          "",
        )}</select></label><label>搜索图片<input id="asset-search" value="${esc(assetQuery)}" placeholder="名称、过程说明或项目"></label></div><label class="check-label"><input id="asset-archived" type="checkbox" ${assetArchived ? "checked" : ""}>查看已收起素材</label><p class="tip">原图保存在本机，包含在完整备份中。收起素材不会删除原图，也不会影响文章。</p><div id="asset-grid" class="asset-grid"></div></section>`;
    $("#asset-filter").value = assetCollection;
    renderAssetGrid(false);
  } catch (e) {
    if (view === "assets")
      $("#main").innerHTML += `<p class="error-inline">${esc(e.message)}</p>`;
  }
}
function renderAssetGrid(picker) {
  const rows = assetRows.filter(
    (a) =>
      Boolean(a.archived) === (picker ? false : assetArchived) &&
      (!assetCollection ||
        (assetCollection === "__unfiled"
          ? !a.collection
          : a.collection === assetCollection)) &&
      `${a.title} ${a.collection} ${a.description}`
        .toLowerCase()
        .includes(assetQuery.toLowerCase()),
  );
  $(picker ? "#asset-picker-grid" : "#asset-grid").innerHTML = rows.length
    ? rows
        .map(
          (a) =>
            `<article class="asset-card"><a href="${esc(a.url)}" target="_blank" rel="noopener"><img src="${esc(a.url)}" alt="${esc(a.title)}" loading="lazy"></a><div><span class="tag ${a.origin === "ai-diagram" ? "purple" : ""}">${a.origin === "ai-diagram" ? "AI 流程示意图" : a.origin === "text-card" ? "正文排版图卡" : a.origin === "imported" ? "历史图片" : "上传原图"}</span><h3>${a.step ? `${a.step}. ` : ""}${esc(a.title)}</h3><small>${esc(a.collection || "未分类")}</small><p class="asset-description">${esc(a.description)}</p><small>已用于 ${a.used_by.length} 篇文章</small><div class="actions spaced"><a class="button small" href="${esc(a.url)}" download>下载原图</a>${picker ? `<button class="button primary small" data-action="asset-insert" data-id="${a.id}">插入正文</button><button class="button small" data-action="asset-cover" data-id="${a.id}">设为封面</button>` : `<button class="button small" data-action="asset-edit" data-id="${a.id}">编辑归档</button><button class="button ghost small" data-action="asset-toggle" data-id="${a.id}">${a.archived ? "恢复显示" : "收起"}</button>`}</div></div></article>`,
        )
        .join("")
    : empty("没有匹配的图片", "上传过程图片，或切换项目与搜索条件。");
}
async function openAssetPicker() {
  assetPickerPost = editor.id;
  assetRows = (await api("/assets")).assets;
  assetCollection = "";
  assetQuery = "";
  modal(
    "从素材库配图",
    `<label>搜索图片<input id="asset-picker-search" placeholder="输入问题、项目或过程说明"></label><div id="asset-picker-grid" class="asset-grid"></div>`,
    '<button class="button" data-action="close">关闭</button>',
  );
  renderAssetGrid(true);
}
function insertAsset(a, cover) {
  if (!editor || editor.id !== assetPickerPost)
    throw new Error("当前文章已变化，请重新打开素材库");
  if (cover) $("#edit-cover").value = a.url;
  else {
    const el = $("#edit-body"),
      alt = a.title.replace(/[\[\]\r\n]/g, " ");
    el.setRangeText(
      `\n![${alt}](${a.url})\n${a.origin === "ai-diagram" ? "\n*AI 生成流程示意图，非实际操作截图。*\n" : ""}`,
      el.selectionStart,
      el.selectionEnd,
      "end",
    );
  }
  markDirty();
  preview();
  closeModal();
  toast("图片已插入，请保存稿件");
}
function paintDiagram(proposal) {
  const canvas = document.createElement("canvas");
  canvas.width = 1200;
  canvas.height = 240 + proposal.steps.length * 178;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#f1f5fb";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  function lines(text, x, y, width, size, color, maxLines) {
    ctx.font = `500 ${size}px "Microsoft YaHei", sans-serif`;
    ctx.fillStyle = color;
    let line = "",
      rows = [];
    for (const ch of text) {
      if (ctx.measureText(line + ch).width > width) {
        rows.push(line);
        line = ch;
      } else line += ch;
    }
    rows.push(line);
    rows
      .slice(0, maxLines)
      .forEach((row, i) => ctx.fillText(row, x, y + i * (size + 10)));
  }
  lines(proposal.title, 64, 75, 1060, 36, "#182a4a", 2);
  proposal.steps.forEach((step, i) => {
    const y = 150 + i * 178;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(64, y, 1072, 146);
    ctx.fillStyle = "#2563eb";
    ctx.fillRect(64, y, 8, 146);
    lines(String(i + 1).padStart(2, "0"), 95, y + 55, 70, 30, "#2563eb", 1);
    lines(step.title, 185, y + 45, 900, 28, "#182a4a", 1);
    lines(step.detail, 185, y + 85, 890, 21, "#475569", 2);
    if (i < proposal.steps.length - 1)
      lines("↓", 585, y + 174, 40, 25, "#2563eb", 1);
  });
  lines(
    "AI 生成流程示意图 · 非实际操作截图 · 请核对后使用",
    64,
    canvas.height - 28,
    1060,
    20,
    "#64748b",
    1,
  );
  return canvas.toDataURL("image/png");
}
function readDiagram() {
  return {
    title: $("#diagram-title").value,
    steps: diagramProposal.steps.map((s, i) => ({
      title: $(`#diagram-step-${i}`).value,
      detail: $(`#diagram-detail-${i}`).value,
    })),
  };
}
async function handleAssetAction(button) {
  const action = button.dataset.action,
    id = button.dataset.id;
  if (action === "asset-picker") {
    await openAssetPicker();
    return true;
  }
  if (action === "asset-insert" || action === "asset-cover") {
    insertAsset(
      assetRows.find((a) => a.id === id),
      action === "asset-cover",
    );
    return true;
  }
  if (action === "asset-upload") {
    modal(
      "归档过程图片",
      `<p class="tip">先填写所属问题与说明，再选择图片。每张最多 5 MB，一次最多 30 张。失败时保留已成功归档的图片。</p>${assetFields()}<label>选择过程图片<input id="asset-files" type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif"></label><div id="asset-upload-status" role="status"></div>`,
      '<button class="button primary" data-action="asset-upload-save">上传并归档</button>',
    );
    return true;
  }
  if (action === "asset-upload-save") {
    const fields = readAssetFields(),
      files = [...$("#asset-files").files];
    if (!files.length || files.length > 30)
      throw new Error("请选择 1–30 张图片");
    if (
      !Number.isInteger(fields.step) ||
      fields.step < 0 ||
      fields.step + files.length - 1 > 9999
    )
      throw new Error("请填写有效步骤编号");
    let done = 0;
    modalBusy = true;
    try {
      for (const file of files) {
        if (file.size > 5000000) throw new Error(`${file.name} 超过 5 MB`);
        await api("/media", {
          ...fields,
          title: (fields.title
            ? `${fields.title}${files.length > 1 ? ` ${done + 1}` : ""}`
            : file.name
          ).slice(0, 150),
          step: fields.step ? fields.step + done : 0,
          data: await fileToBase64(file),
        });
        done++;
        $("#asset-upload-status").textContent =
          `已归档 ${done} / ${files.length} 张`;
      }
    } catch (e) {
      $("#asset-files").value = "";
      throw new Error(`已归档 ${done} 张。${e.message}。请重新选择剩余图片。`);
    } finally {
      modalBusy = false;
    }
    closeModal();
    toast(`已归档 ${done} 张图片`);
    return true;
  }
  if (action === "asset-edit") {
    const a = assetRows.find((a) => a.id === id);
    modal(
      "编辑图片归档",
      `<img class="asset-edit-preview" src="${esc(a.url)}" alt="${esc(a.title)}">${assetFields(a)}<p class="tip">修改说明不会改变已插入文章的文字；图片地址保持不变。</p>${a.used_by.map((p) => `<p>用于：${esc(p.title)}</p>`).join("")}`,
      `<button class="button primary" data-action="asset-save" data-id="${id}">保存归档</button>`,
    );
    return true;
  }
  if (action === "asset-save") {
    await api(`/assets/${id}`, readAssetFields());
    closeModal();
    return true;
  }
  if (action === "asset-toggle") {
    const a = assetRows.find((a) => a.id === id);
    await api(`/assets/${id}`, { archived: !a.archived });
    await drawAssets();
    return true;
  }
  if (action === "diagram") {
    await saveEditor(true);
    if (dirty) throw new Error("请先保存当前稿件");
    diagramPost = editor.id;
    diagramProposal = null;
    assetRows = (await api("/assets")).assets;
    const count = (editor.body.match(/!\[[^\]]*\]\(/g) || []).length;
    modal(
      "AI 流程配图",
      `<p class="tip">当前正文有 ${count} 张图片。文字模型根据已保存正文提炼 2–6 个步骤，再由本机排版绘制 PNG 示意图，不调用图片模型。适合排查流程和教程，不能代替真实过程截图。</p><div id="diagram-planned-model"></div><label>配图重点<textarea id="diagram-instruction" rows="3" maxlength="3000">把文章中的问题排查或操作步骤整理成清晰流程；缺少依据标注待验证。</textarea></label><div id="diagram-result"></div>`,
      `<button class="button primary" data-action="diagram-generate" ${state.ai_ready ? "" : "disabled"}>${state.ai_ready ? "生成流程示意图" : "请先配置辅助写作模型"}</button>`,
    );
    if (typeof renderPlannedModel === 'function') void renderPlannedModel($('#diagram-planned-model'), 'diagram');
    return true;
  }
  if (action === "diagram-generate") {
    modalBusy = true;
    button.textContent = "正在提炼步骤…";
    try {
      diagramProposal = await api("/ai/diagram", {
        post_id: diagramPost,
        instruction: $("#diagram-instruction").value,
      });
      $("#diagram-result").innerHTML =
        `${typeof modelProvenanceHtml === 'function' ? modelProvenanceHtml(diagramProposal.model_provenance) + modelProvenanceHtml(diagramProposal.layout_model) : ''}<p class="tip">先核对并修改步骤，再点击刷新预览。刷新预览只重新排版，不调用模型。确认入库后才能插入文章。</p><label>图标题<input id="diagram-title" maxlength="40" value="${esc(diagramProposal.title)}"></label>${diagramProposal.steps.map((s, i) => `<label>步骤 ${i + 1}<input id="diagram-step-${i}" maxlength="24" value="${esc(s.title)}"></label><label>说明 ${i + 1}<input id="diagram-detail-${i}" maxlength="80" value="${esc(s.detail)}"></label>`).join("")}<button class="button" data-action="diagram-preview">刷新预览</button><img id="diagram-preview" class="asset-edit-preview" alt="AI 流程示意图预览" src="${paintDiagram(diagramProposal)}">${assetFields({ title: diagramProposal.title, collection: "AI 流程示意图", description: diagramAssetDescription(diagramProposal) })}<button class="button primary" data-action="diagram-save">确认入库并插入正文</button>`;
    } finally {
      modalBusy = false;
      button.textContent = "重新生成流程示意图";
    }
    return true;
  }
  if (action === "diagram-preview") {
    $("#diagram-preview").src = paintDiagram(readDiagram());
    return true;
  }
  if (action === "diagram-save") {
    if (editor?.id !== diagramPost)
      throw new Error("当前文章已变化，请重新生成");
    const proposal = readDiagram();
    if (!proposal.title.trim() || proposal.steps.some((s) => !s.title.trim()))
      throw new Error("请补全图标题和各步骤标题");
    const fields = readAssetFields();
    const result = await api("/media", {
      ...fields,
      origin: "ai-diagram",
      data: paintDiagram(proposal).split(",")[1],
    });
    assetPickerPost = diagramPost;
    insertAsset(
      { ...result, title: fields.title, origin: "ai-diagram" },
      false,
    );
    return true;
  }
  return false;
}
document.addEventListener("input", (e) => {
  if (e.target.id === "asset-search" || e.target.id === "asset-picker-search") {
    assetQuery = e.target.value;
    renderAssetGrid(e.target.id === "asset-picker-search");
  }
});
document.addEventListener("change", (e) => {
  if (e.target.id === "asset-filter") {
    assetCollection = e.target.value;
    renderAssetGrid(false);
  }
  if (e.target.id === "asset-archived") {
    assetArchived = e.target.checked;
    renderAssetGrid(false);
  }
});
