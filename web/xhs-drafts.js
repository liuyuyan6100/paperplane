function xhsDraftSettings(s) {
  return `<section class="panel"><div class="panel-head"><h2>小红书自动送草稿</h2></div><div class="panel-body"><p>纸飞机自动上传图集、填写标题与文案并保存草稿。你打开草稿检查，最后在小红书确认发布。</p><p class="tip">首次在弹出的小红书窗口登录一次。草稿保存在这个窗口的浏览器中，请从纸飞机打开；普通 Edge、手机和云端不会共用这份草稿。</p><button class="button" data-action="xhs-browser-login" ${s.available?'':'disabled'}>打开小红书 / 登录</button><span id="xhs-browser-progress" role="status">${esc(s.error || s.progress || (s.available?'首次送稿时也会自动打开登录窗口。':'请使用 Windows 安装版的本机模式。'))}</span></div></section>`;
}

function xhsDraftJob(j) {
  if (j.channel_id !== 'xiaohongshu' || j.deleted_at) return '';
  const s = j.xhs_browser || {}, mode = j.snapshot.delivery_config?.mode;
  if (!s.available) return '<p class="notice">自动保存小红书草稿需要 Windows 安装版的本机模式。</p>';
  if (j.status === 'manual') return `<div class="notice blue"><p>点击下方按钮，纸飞机会自动适配标题、上传全部图片并保存草稿，之后你只需检查和确认发布。</p><label>小红书标题（可选修改）<input id="xhs-delivery-title" value="${esc(j.snapshot.xhs_delivery?.title || j.snapshot.xhs?.title || '')}"></label><small>标题会自动按小红书1–20字规则适配，无需手动缩短；原稿、正文与图片保留。</small><button class="button primary spaced" data-action="xhs-browser-send">自动送入小红书草稿箱</button></div>`;
  if (mode !== 'xhs-browser') return '';
  if (['queued','running'].includes(j.status)) {
    clearTimeout(xhsJobTimer);
    xhsJobTimer = setTimeout(() => {
      if ($('#modal').open && activeJob?.id === j.id) showJob(j.id, true).catch(e=>toast(e.message));
    }, 1500);
  }
  if (['draft','uncertain'].includes(j.status)) return `<div class="notice blue"><p>${esc(j.note || '请先核对小红书草稿。')}</p><button class="button primary" data-action="xhs-browser-draft">${j.status==='draft'?'打开小红书草稿':'打开小红书核对草稿'}</button><p class="tip">草稿保存在纸飞机的小红书窗口。确认发布由你操作，不会自动公开发表。</p><span id="xhs-browser-progress" role="status"></span></div>`;
  if (['queued','running'].includes(j.status)) return `<div class="notice blue">${esc(s.progress || '正在准备小红书草稿…')}<p>首次使用请在弹出的窗口登录；完成后自动继续。这里会自动更新保存结果。</p></div>`;
  return '';
}

let xhsStatusTimer, xhsJobTimer;
function pollXhsBrowser() {
  clearTimeout(xhsStatusTimer);
  async function poll() {
    try {
      const s = await api('/xhs-drafts');
      const el = $('#xhs-browser-progress');
      if (el) el.textContent = s.error || s.progress;
      if (s.busy) xhsStatusTimer = setTimeout(poll, 1500);
      else { if(s.error)toast(s.error); await refresh(); }
    } catch(e) {toast(e.message);}
  }
  xhsStatusTimer = setTimeout(poll, 600);
}

async function handleXhsDraftAction(button) {
  const action = button.dataset.action;
  if (!['xhs-browser-login','xhs-browser-send','xhs-browser-draft'].includes(action)) return false;
  button.disabled = true;
  try {
    if (action === 'xhs-browser-send') {
      const title = $('#xhs-delivery-title').value.trim();
      await api(`/jobs/${activeJob.id}/xhs-send`, {title});
      await showJob(activeJob.id);
      toast('已加入自动送草稿队列；首次使用请在弹出窗口登录。');
      await refresh();
    } else {
      await api(action === 'xhs-browser-login' ? '/xhs-drafts/open' : `/jobs/${button.dataset.id || activeJob.id}/xhs-open`, {});
      toast('正在打开纸飞机的小红书窗口。');
      pollXhsBrowser();
    }
  } finally {button.disabled=false;}
  return true;
}
