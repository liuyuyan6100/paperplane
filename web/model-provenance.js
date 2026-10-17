"use strict";
const plannedModelRequests = new WeakMap();

async function renderPlannedModel(container, task) {
  const node = typeof container === 'string' ? document.querySelector(container) : container;
  if (!node) return;
  const request = {};
  plannedModelRequests.set(node, request);
  node.textContent = '正在读取本阶段模型…';
  try {
    const response = await api('/model-provenance/planned');
    if (!node.isConnected || plannedModelRequests.get(node) !== request) return;
    const tasks = Array.isArray(response) ? response : response.tasks;
    const value = Array.isArray(tasks) ? tasks.find(item => item.task === task) : null;
    if (!value) throw new Error('未返回本阶段模型');
    node.innerHTML = modelProvenanceHtml(value);
  } catch (_error) {
    if (node.isConnected && plannedModelRequests.get(node) === request) node.textContent = '暂无法读取计划模型；可在模型设置中查看。';
  }
}

// Only this projection is shown. Never stringify route/provider snapshots in the UI.
function modelProvenanceHtml(value) {
  if (!value || typeof value !== 'object') return '<p class="tip model-provenance">模型来源：历史记录未保存</p>';
  const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label = escape(value.label || '本阶段');
  if (value.source === 'none') return `<p class="tip model-provenance">${label} · 未调用模型</p>`;
  const requested = escape(value.requested_model || (value.mode === 'program' ? '外部程序，模型未记录' : value.source === 'planned' ? '未配置' : '未记录'));
  if (value.source === 'planned') return `<p class="tip model-provenance">${label} · 计划使用：${requested}${value.mode === 'program' ? '（外部程序）' : ''}</p>`;
  if (value.source === 'unknown') return `<p class="tip model-provenance">${label} · 历史记录未保存模型名称</p>`;
  if (value.source === 'legacy') return `<p class="tip model-provenance">${label} · 请求模型：${requested}<br>来自当时任务快照；未记录接口返回模型。</p>`;
  const states = {running:'调用中',completed:'调用已完成',failed:'调用失败',interrupted:'调用已中断'};
  const summary = `${label} · 请求模型：${requested} · ${escape(states[value.status] || '已记录')}`;
  const calls = Array.isArray(value.calls) ? value.calls : [];
  const rows = calls.map(call => `<li>第 ${escape(call.attempt)} 次 · ${escape(states[call.status] || '已记录')}<br>请求：${escape(call.requested_model || '未记录')}；接口返回：${escape(call.reported_model || '未提供')}<br>${escape(call.started_at || '')}${call.finished_at ? ` → ${escape(call.finished_at)}` : ''}</li>`).join('');
  return `<div class="tip model-provenance"><p>${summary}</p><details><summary>查看模型调用记录${calls.length ? `（${calls.length} 次）` : ''}</summary><ul>${rows}</ul><p>“接口返回”仅指服务响应中的模型标识；不代表已核实网关背后的实际模型。</p></details></div>`;
}
