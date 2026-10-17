"use strict";
let desktopUpdateState = null;

function desktopUpdatePanel() {
  return '<section id="desktop-updates" class="panel" hidden><div class="panel-head"><h2>软件更新</h2></div><div class="panel-body" id="desktop-update-body"></div></section>';
}

async function drawDesktopUpdates() {
  $("#main").innerHTML = heading("软件更新", "更新由你发起，安装由你确认。") + desktopUpdatePanel();
  await refreshDesktopUpdates();
  if (!desktopUpdateState?.enabled && view === "updates")
    $("#main").innerHTML += '<p class="tip">请在已安装的纸飞机客户端中使用软件更新。</p>';
}

function renderDesktopUpdates() {
  const state = desktopUpdateState;
  const panel = $("#desktop-updates");
  if (panel) panel.hidden = !state?.enabled;
  if (!state?.enabled) return;
  const busy = ["checking", "downloading", "installing"].includes(state.status);
  const downloadable = ["available", "error"].includes(state.status) && state.update_available;
  const html = `<details ${!state.server_url ? "open" : ""}><summary>私有更新服务器</summary><label class="spaced">服务器地址<input id="desktop-update-server" value="${esc(state.server_url || "")}" placeholder="https://updates.example.com" ${busy ? "disabled" : ""}></label><label>更新授权码<input id="desktop-update-token" type="password" autocomplete="new-password" placeholder="${state.key_set ? "已配置，留空保留；换服务器需重新填写" : "填写服务器生成的专用更新授权码"}" ${busy ? "disabled" : ""}></label><button class="button" data-desktop-update="configure" ${busy ? "disabled" : ""}>保存并检查更新</button><p class="tip">更新授权独立于模型密钥，仅用于你的私有更新服务。</p></details><p>当前客户端：<strong>${esc(state.current_version)}</strong>${state.latest_version ? ` · 最新发布：${esc(state.latest_version)}` : ""}</p><p role="status" aria-live="polite">${esc(state.message)}</p>${state.status === "downloading" ? `<progress max="100" value="${Number(state.progress) || 0}" aria-label="下载进度"></progress> ${Number(state.progress) || 0}%` : ""}${state.notes ? `<details open><summary>更新说明</summary><p class="desktop-release-notes">${esc(state.notes)}</p></details>` : ""}<div class="actions spaced"><button class="button" data-desktop-update="check" ${busy ? "disabled" : ""}>检查更新</button>${downloadable ? '<button class="button primary" data-desktop-update="download">下载并打开安装向导</button>' : ""}${state.status === "downloaded" ? '<button class="button primary" data-desktop-update="install">打开安装向导</button>' : ""}</div><p class="tip spaced">启动时自动检查，不自动下载。下载校验通过后，会先检查稿件和任务，再关闭工作台并打开安装向导，由你确认安装。沿用原安装目录，保留数据和配置。</p>`;
  const body = $("#desktop-update-body");
  if (body && body.dataset.rendered !== html) {body.innerHTML = html; body.dataset.rendered = html;}
  const notice = $("#desktop-update-notice");
  if (notice) {
    notice.hidden = view === "updates" || !["available", "downloaded", "downloading"].includes(state.status);
    notice.querySelector("span").textContent = state.message;
  }
}

async function refreshDesktopUpdates() {
  try {
    desktopUpdateState = await api("/desktop/update");
    renderDesktopUpdates();
  } catch {
    // A temporary connection failure leaves the last status and draft intact.
  }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-desktop-update]");
  if (!button || button.disabled) return;
  button.disabled = true;
  try {
    const values = button.dataset.desktopUpdate === "configure"
      ? {server_url: $("#desktop-update-server").value, token: $("#desktop-update-token").value} : {};
    desktopUpdateState = await api("/desktop/update/" + button.dataset.desktopUpdate, values);
    if (button.dataset.desktopUpdate === "configure") $("#desktop-update-token").value = "";
    renderDesktopUpdates();
  } catch (error) {toast(error.message);}
  finally {if (button.isConnected) button.disabled = false;}
});

document.addEventListener("DOMContentLoaded", () => {
  const notice = document.createElement("div");
  notice.id = "desktop-update-notice";
  notice.className = "desktop-update-notice";
  notice.hidden = true;
  notice.innerHTML = '<span></span><button class="button small" data-desktop-update="open">查看更新</button>';
  $("#main").before(notice);
  let first = true;
  const timer = setInterval(async () => {
    await refreshDesktopUpdates();
    if (first && desktopUpdateState?.enabled === false) clearInterval(timer);
    first = false;
  }, 1500);
  refreshDesktopUpdates();
});
