"use strict";
let modelSettingsState = null, modelCatalog = [], modelCatalogSequence = 0;
const modelTabs = {text:'文案与脚本', transcription:'语音转写', speech:'配音', image:'图片', video:'视频', music:'音乐'};

function modelStatus(task) {
  return task.tested ? task.test_note : task.configured ? '已配置，未自测' : '待配置';
}
function modelTaskRow(task, image = false) {
  return `<div class="model-task-row"><div><b>${esc(task.label)}</b><small data-model-status="${task.id}">${esc(modelStatus(task))}</small></div><label>模型名称<input data-model-task="${task.id}" data-model-kind="${image?'image':'text'}" value="${esc(task.model)}" list="${image?'model-image-options':'model-text-options'}" placeholder="${image?'留空沿用默认图片模型':'留空沿用默认文字模型'}"><small>当前使用：<span data-model-effective="${task.id}">${esc(task.effective_model || '未配置')}</span></small></label><button class="button small" data-action="model-test" data-task="${task.id}">自测本项</button></div>`;
}
function modelSettingsPanel(settings, legacy) {
  modelSettingsState = settings;
  if (settings.setup) renderModelSetup(settings.setup);
  modelCatalog = [];
  modelCatalogSequence++;
  return `<section class="panel model-settings" id="model-settings-panel"><div class="panel-head"><h2>模型与能力</h2><button class="button small" data-wizard="open">分步配置</button><button class="button primary small" data-action="save-model-settings">保存模型配置</button></div><div class="panel-body">
    <div id="model-setup-help" role="status">${modelSetupHelp(settings.setup)}</div>
    <p class="tip">统一连接填写一次，各功能独立选择模型。模型目录是当前调用密钥可访问的范围；目录发现不代表接口和权限已验证。</p>
    <details class="spaced"><summary>调用方式与本机兼容设置</summary>${programSettings(legacy)}</details>
    <div id="ai-api-fields" ${legacy.ai_mode==='program'?'hidden':''}>
      <div class="model-connection"><label>统一接口地址<input id="ai-base" value="${esc(settings.connection.base_url)}" placeholder="https://api.example.com/v1"><small>使用 sub2api 综合分组的调用密钥；不要填写管理员管理密钥。</small></label>
      <label>统一 API Key<input type="password" id="ai-key" autocomplete="new-password" placeholder="${settings.connection.key_set?'已配置，留空保留':'填写模型调用密钥'}"></label>
      <label>默认文字模型<input id="ai-model" value="${esc(settings.connection.model)}" list="model-text-options" placeholder="填写模型 ID"></label></div>
      <div class="actions"><button class="button" data-action="model-refresh" data-source="shared">刷新统一模型列表</button><small id="model-catalog-status" role="status">可手动输入模型 ID；刷新和选择不会自动保存或切换。</small></div>
      <datalist id="model-text-options"></datalist><datalist id="model-image-options"></datalist>
      <nav class="model-tabs" aria-label="模型能力通道">${Object.entries(modelTabs).map(([id,label])=>`<button class="button ${id==='text'?'primary':''}" data-action="model-tab" data-tab="${id}" aria-pressed="${id==='text'}">${label}</button>`).join('')}</nav>
      <div data-model-channel="text"><p class="tip">未单独填写的模块沿用默认文字模型。更改设置后，新开始的请求使用新配置；已发出的请求继续使用启动时模型。</p>${settings.text_tasks.map(t=>modelTaskRow(t)).join('')}<div class="model-pending"><b>口播脚本工作流 · 待接入</b><p>后续将关联脚本、配音、字幕与画面，目前不会把写作任务当作完整口播制作。</p></div></div>
      <div data-model-channel="image" hidden><label class="check-label spaced"><input type="checkbox" id="model-image-shared" ${settings.image.use_shared?'checked':''}>图片使用统一接口与 API Key</label>
      <div id="model-image-independent" class="model-connection" ${settings.image.use_shared?'hidden':''}><label>独立图片接口地址<input id="model-image-base" value="${esc(settings.image.base_url)}" placeholder="https://…/v1"></label><label>独立图片 API Key<input id="model-image-key" type="password" autocomplete="new-password" placeholder="${settings.image.key_set?'已配置，留空保留':'填写图片服务密钥'}"></label><button class="button" data-action="model-refresh" data-source="image">刷新图片接口模型列表</button></div>
      <label>默认图片模型<input id="model-image-default" value="${esc(settings.image.model)}" list="model-image-options" placeholder="必须使用支持图片生成的模型"></label><label>参考图接口<select id="model-image-references"><option value="edits" ${settings.image.references==='edits'?'selected':''}>支持真实参考图和 images/edits</option><option value="none" ${settings.image.references==='none'?'selected':''}>仅文生图 images/generations</option></select></label>${settings.image_tasks.map(t=>modelTaskRow(t,true)).join('')}
      <p class="tip">图片自测会生成一张测试图片，可能产生费用；结果不写入文章。文生图通过不代表参考图编辑已验证。模型列表中的图片名称仅是候选。</p></div>
      ${['transcription','speech','video','music'].map(id=>`<div data-model-channel="${id}" hidden><div class="model-pending"><h3>${modelTabs[id]} · 待接入</h3><p>此通道尚未接通对应接口和任务流程。即使综合目录中出现相关模型，也需完成接入和权限验证后才能配置使用。</p><button class="button" disabled>暂不可配置或自测</button></div></div>`).join('')}
    </div><p class="tip spaced">文字自测只发送“连接测试”，会使用服务额度。失败后不自动切换备用模型。配置随当前运行端保存。</p><p id="model-settings-result" role="status" aria-live="polite"></p></div></section>`;
}
function modelSettingsValues() {
  return {connection:{...($('#ai-mode').value==='api'?{mode:'api'}:{}),base_url:$('#ai-base').value, api_key:$('#ai-key').value, model:$('#ai-model').value},
    text_models:Object.fromEntries($$('[data-model-kind="text"]').map(el=>[el.dataset.modelTask,el.value])),
    image:{use_shared:$('#model-image-shared').checked, base_url:$('#model-image-base').value,
      api_key:$('#model-image-key').value, model:$('#model-image-default').value,
      cover_model:$('[data-model-task="cover"]').value, illustration_model:$('[data-model-task="illustration"]').value,
      references:$('#model-image-references').value}};
}
function updateModelStatus(settings) {
  modelSettingsState = settings;
  if (settings.setup) renderModelSetup(settings.setup);
  for (const task of [...settings.text_tasks,...settings.image_tasks]) {
    const status=$(`[data-model-status="${task.id}"]`), effective=$(`[data-model-effective="${task.id}"]`);
    if(status) status.textContent=modelStatus(task);
    if(effective) effective.textContent=task.effective_model || '未配置';
  }
}
async function saveModelSettings() {
  const result=await api('/model-settings',modelSettingsValues());
  updateModelStatus(result);
  $('#ai-key').value='';$('#model-image-key').value='';
  $('#ai-key').placeholder=result.connection.key_set?'已配置，留空保留':'填写模型调用密钥';
  $('#model-settings-result').textContent='模型配置已保存。';
  return result;
}
function fillModelCatalog(result, source) {
  const imagePattern=/image|dall[-_]?e|flux|hunyuan-image|grok-imagine/i;
  const nonTextPattern=/image|video|seedance|speech|tts|whisper|music|audio|dall[-_]?e|flux|sora/i;
  modelCatalog=result.models;
  if(source==='shared') $('#model-text-options').innerHTML=result.models.filter(id=>!nonTextPattern.test(id)).map(id=>`<option value="${esc(id)}"></option>`).join('');
  if(source==='image' || $('#model-image-shared').checked) $('#model-image-options').innerHTML=result.models.filter(id=>imagePattern.test(id)&&!(/video/i.test(id))).map(id=>`<option value="${esc(id)}"></option>`).join('');
  $('#model-catalog-status').textContent=`已读取 ${result.models.length} 个当前密钥可见模型。名称仅辅助筛选；未知能力仍可手动填写并单项自测。`;
}
async function handleModelSettingsAction(button) {
  const action=button.dataset.action;
  if(!['model-tab','model-refresh','save-model-settings','model-test'].includes(action)) return false;
  if(action==='model-tab') {
    $$('[data-model-channel]').forEach(el=>el.hidden=el.dataset.modelChannel!==button.dataset.tab);
    $$('[data-action="model-tab"]').forEach(el=>{const selected=el===button;el.classList.toggle('primary',selected);el.setAttribute('aria-pressed',String(selected));});
    return true;
  }
  button.disabled=true;
  const status=$('#model-settings-result');
  try {
    if(action==='model-refresh') {
      const sequence=++modelCatalogSequence, source=button.dataset.source;
      status.textContent='正在读取模型目录…';
      const image=source==='image';
      const base_url=$(image?'#model-image-base':'#ai-base').value, api_key=$(image?'#model-image-key':'#ai-key').value;
      const result=await api(image?'/illustrations/models':'/ai/models',{base_url,api_key,...(image?{connection:'independent'}:{})});
      if(sequence!==modelCatalogSequence || !$('#model-settings-panel')) return true;
      fillModelCatalog(result,source);status.textContent='目录已刷新，配置尚未保存。';
    } else {
      await saveModelSettings();
      if(action==='model-test') {
        status.textContent='正在自测本项，请等待结果…';
        const result=await api('/model-settings/test',{task:button.dataset.task});
        updateModelStatus(await api('/model-settings'));
        status.textContent=`${result.model}：${result.note}`;
      }
    }
  } catch(error) {
    status.textContent=error.message;
    if(action==='model-test') {
      try {updateModelStatus(await api('/model-settings'));} catch(_) { /* Keep the original test error. */ }
    }
  }
  finally {button.disabled=false;}
  return true;
}
document.addEventListener('change',event=>{
  if(event.target.id==='model-image-shared') {
    $('#model-image-independent').hidden=event.target.checked;
    modelCatalogSequence++;$('#model-image-options').innerHTML='';
    $('#model-catalog-status').textContent='图片连接方式已调整，请刷新对应目录。';
  }
});
document.addEventListener('input',event=>{
  if(['ai-base','ai-key','model-image-base','model-image-key'].includes(event.target.id)) {
    modelCatalogSequence++;modelCatalog=[];
    $('#model-text-options').innerHTML='';$('#model-image-options').innerHTML='';
    $('#model-catalog-status').textContent='地址或密钥已调整，请重新刷新模型列表。';
  }
  if(event.target.matches('[data-model-task],#ai-base,#ai-key,#ai-model,#model-image-default,#model-image-base,#model-image-key')) {
    $$('[data-model-status]').forEach(el=>el.textContent='有未保存修改，保存后可自测');
  }
});


let modelSetupState = null, modelSetupFocus = false, modelSetupSequence = 0;
function modelSetupHelp(info) {
  if (!info || info.configured) return '';
  return `<div class="model-setup-help"><b>先连接一个文字模型</b><p>${info.missing.map(item=>esc(item.label)).join('；')}。</p><p>填写接口地址、调用密钥和默认文字模型后，点击“保存模型配置”。可先刷新模型列表，也可手动填写模型名称。本机无需认证的接口可以不填密钥。</p><p>保存后立即供新请求使用，无需重启。需要验证时点击“自测本项”；仅检查配置不会调用模型。图片等能力可稍后配置。</p></div>`;
}
function renderModelSetup(info) {
  modelSetupSequence++;
  modelSetupState = info;
  let banner = $('#model-setup-notice');
  if (!banner) {
    banner = document.createElement('div');banner.id='model-setup-notice';banner.className='model-setup-notice';
    banner.setAttribute('role','status');$('#main').before(banner);
  }
  banner.hidden = !!info.configured;
  banner.innerHTML = info.configured ? '' : `<div><b>${info.read_error?'模型配置读取失败':'文字模型尚未配置完整'}</b><span>可先整理内容，需要 AI 创作时再配置。</span></div><button class="button small" data-setup="open">去配置模型</button>`;
  const help=$('#model-setup-help');if(help)help.innerHTML=modelSetupHelp(info);
  if(info.configured) $('#model-setup-dialog')?.close();
}
async function checkModelSetup(prompt=false) {
  const sequence=++modelSetupSequence;
  try {
    const info=await api('/model-setup');
    if(sequence!==modelSetupSequence)return;
    renderModelSetup(info);
    if(prompt && info.should_prompt && view==='home' && !usageGuide?.active && !dirty && !$('#modal').open && !document.querySelector('#model-wizard')?.open && !(typeof modelWizardOpening!=='undefined' && modelWizardOpening)) {
      let dialog=$('#model-setup-dialog');
      if(!dialog) {
        dialog=document.createElement('dialog');dialog.id='model-setup-dialog';dialog.setAttribute('aria-labelledby','model-setup-title');
        dialog.innerHTML=`<div class="panel-body"><h2 id="model-setup-title">连接模型，开始 AI 创作</h2><p>还差一步：配置文字模型后，即可使用对话安排、选题和写作。</p><div id="model-setup-missing"></div><p>只检查当前工作台配置，不会发送内容或调用模型。也可以稍后从“设置 → 模型与能力”完成。</p><p class="model-setup-error" role="alert"></p><div class="actions"><button class="button" data-setup="later">稍后配置</button><button class="button primary" data-setup="open">去配置模型</button></div></div>`;
        dialog.addEventListener('cancel',event=>{event.preventDefault();void dismissModelSetup();});document.body.append(dialog);
      }
      $('#model-setup-missing').textContent=info.missing.map(item=>item.label).join('；');dialog.showModal();
    }
  } catch (_) { /* A failed check is not proof that configuration is missing. */ }
}
async function dismissModelSetup() {
  const dialog=$('#model-setup-dialog');
  try {const info=await api('/model-setup/dismiss',{});modelSetupSequence++;renderModelSetup(info);dialog?.close();}
  catch(error){if(dialog)dialog.querySelector('[role="alert"]').textContent=error.message;}
}
function focusModelSetup() {
  if(!modelSetupFocus || view!=='settings')return;
  const panel=$('#model-settings-panel');if(!panel)return;
  modelSetupFocus=false;
  const field=$('#'+(modelSetupState?.missing[0]?.field || 'ai-base'));
  const details=field?.closest('details');if(details)details.open=true;
  panel.scrollIntoView({block:'start'});field?.focus({preventScroll:true});
}
new MutationObserver(focusModelSetup).observe(document.querySelector('#main'),{childList:true});
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-setup]');if(!button)return;
  if(button.dataset.setup==='later'){void dismissModelSetup();return;}
  if(dirty || profileDirty || repurposeDirty || illustrationDirty || sharedLibraryUploading){toast('请先保存当前修改或完成导入，再配置模型');return;}
  $('#model-setup-dialog')?.close();
  if(typeof openModelWizard==='function'){void openModelWizard();return;}
  modelSetupFocus=true;
  if(view==='settings')focusModelSetup();else location.hash='#settings';
});
