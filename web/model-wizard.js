"use strict";
let modelWizard = null, modelWizardOpening = false;
function wizardDrafts(s) {
  return {text:{base_url:s.connection.base_url,api_key:'',model:s.connection.model},
    image:{...s.image,api_key:'',cover_model:s.image_tasks.find(t=>t.id==='cover')?.model || '',illustration_model:s.image_tasks.find(t=>t.id==='illustration')?.model || ''}};
}
function wizardPageDirty() {
  if (!$('#model-settings-panel') || !modelSettingsState) return false;
  const values=modelSettingsValues(), saved=wizardDrafts(modelSettingsState);
  return Object.keys(saved.text).some(k=>values.connection[k]!==saved.text[k]) ||
    Object.keys(values.image).some(k=>values.image[k]!==saved.image[k]) ||
    modelSettingsState.text_tasks.some(t=>values.text_models[t.id]!==t.model) || $('#ai-mode').value!==modelSettingsState.mode;
}
async function openModelWizard() {
  if (modelWizardOpening || $('#model-wizard')?.open) return;
  if(dirty || profileDirty || repurposeDirty || illustrationDirty || sharedLibraryUploading || wizardPageDirty()) {
    toast('请先保存当前修改，再打开模型配置向导');return;
  }
  if($('#modal').open){toast('请先完成或关闭当前操作，再打开模型配置向导');return;}
  modelWizardOpening=true;
  try {
    const saved=await api('/model-settings');
    let dialog=$('#model-wizard');
    if(!dialog){
      dialog=document.createElement('dialog');dialog.id='model-wizard';dialog.setAttribute('aria-labelledby','wizard-title');document.body.append(dialog);
      dialog.addEventListener('cancel',e=>{e.preventDefault();closeModelWizard();});
      dialog.addEventListener('input',()=>{if(modelWizard){captureWizard();modelWizard.dirty=true;}});
      dialog.addEventListener('change',e=>{if(!modelWizard)return;captureWizard();modelWizard.dirty=true;
        if(e.target.id==='wizard-image-shared')drawModelWizard();
        if(e.target.id==='wizard-catalog-choice' && e.target.value){const target=$('#wizard-model');target.value=e.target.value;captureWizard();}
      });
    }
    modelWizard={step:0,saved,drafts:wizardDrafts(saved),dirty:false,busy:false};
    $('#model-setup-dialog')?.close();drawModelWizard();dialog.showModal();
  } catch(error){toast(error.message);} finally {modelWizardOpening=false;}
}
function wizardInput(label,id,value,hint='',type='text') {
  return `<label>${label}<input id="${id}" type="${type}" value="${esc(value)}" ${type==='password'?'autocomplete="new-password"':''}>${hint?`<small>${esc(hint)}</small>`:''}</label>`;
}
function wizardStatus(t) { return t.tested?'自测通过':t.configured?'已配置 · 未自测':'待配置'; }
function drawModelWizard(message='') {
  const w=modelWizard;if(!w)return;
  const image=w.step===1,d=image?w.drafts.image:w.drafts.text,s=w.saved;
  let body='';
  if(w.step<2) {
    body=`<h3>${image?'选择生图模型':'连接文字模型'}</h3><p class="muted">${image?'用于封面、正文插图与图集。文字模型不一定能生图，可使用独立的图片接口，也可以稍后配置。':'用于对话安排、选题、提纲、正文和审稿。已有配置会自动带入，密钥留空表示保留。'}</p>`;
    if(!image && s.mode!=='api')body+='<p class="model-setup-help">当前使用旧程序模式。点击保存后改用模型接口（API）。</p>';
    if(image)body+=`<label class="check-label"><input id="wizard-image-shared" type="checkbox" ${d.use_shared?'checked':''}>复用已保存的文字接口与密钥</label>${d.use_shared?`<p class="tip">当前复用：${esc(s.connection.base_url || '文字接口尚未保存')}。图片模型名称仍需单独填写。</p>`:''}`;
    if(!image || !d.use_shared)body+=wizardInput('接口地址','wizard-base',d.base_url,'例如 https://你的服务地址/v1')+wizardInput('API Key','wizard-key','',(image?s.image.key_set:s.connection.key_set)?'已保存密钥，留空保留；更换地址时填写新密钥。':'填写服务提供的调用密钥。本机无需认证的接口可留空。','password');
    body+=wizardInput(image?'默认生图模型':'默认文字模型','wizard-model',d.model,'填写模型 ID，或从下方列表选择。')+
      `<div class="wizard-catalog"><button class="button" data-wizard="catalog">获取模型列表</button><select id="wizard-catalog-choice" aria-label="选择可用模型"><option value="">也可直接手动输入</option></select></div><p class="tip">获取列表不会保存配置；列表里的名称不代表已验证能力。</p>`;
    if(image)body+=`<details><summary>封面、插图与参考图设置（可选）</summary>${wizardInput('封面模型','wizard-cover',d.cover_model,'留空沿用默认生图模型。')}${wizardInput('插图与图集模型','wizard-illustration',d.illustration_model,'留空沿用默认生图模型。')}<label>参考图能力<select id="wizard-references"><option value="edits" ${d.references==='edits'?'selected':''}>支持参考图编辑</option><option value="none" ${d.references==='none'?'selected':''}>仅文生图</option></select></label><p class="tip">请按接口实际能力选择；文生图自测通过不代表参考图编辑已验证。</p></details>`;
    if(!image && s.text_tasks.some(t=>t.model))body+='<p class="tip">已有按功能单独选择的模型继续保留；更改默认模型只影响未单独指定的功能。</p>';
    body+=`<div class="wizard-test"><p class="tip">${image?'自测会保存本步配置并生成一张测试图片，可能产生费用，不写入文章。':'自测会保存本步配置并发送一条连接测试，可能消耗服务额度，不发送你的文章。'}</p><button class="button" data-wizard="test">${image?'保存并自测封面生图':'保存并自测文字'}</button></div>`;
  } else {
    body=`<h3>检查配置与能力</h3><p class="muted">已配置表示字段完整；自测通过才表示这项接口曾成功调用。你可以随时重新打开向导。</p><div class="wizard-summary"><h4>文字模型</h4>${s.text_tasks.map(t=>`<div><span>${esc(t.label)}<small>${esc(t.effective_model || '未填写')}</small></span><b>${wizardStatus(t)}</b></div>`).join('')}<h4>生图模型</h4>${s.image_tasks.map(t=>`<div><span>${esc(t.label)}<small>${esc(t.effective_model || '未填写')}</small></span><b>${wizardStatus(t)}</b></div>`).join('')}</div><p class="tip">语音转写、配音、视频和音乐暂未接通，当前不能在向导中配置或自测。</p>`;
  }
  $('#model-wizard').innerHTML=`<div class="wizard-head"><div><h2 id="wizard-title">模型配置向导</h2><p>按步骤配置，随时回来调整</p></div><button class="close" data-wizard="close" aria-label="退出向导">×</button></div><ol class="wizard-steps" aria-label="配置进度">${['文字模型','生图模型','检查配置'].map((name,i)=>`<li ${i===w.step?'aria-current="step"':''}><span>${i+1}</span>${name}</li>`).join('')}</ol><div class="wizard-body">${body}<p id="wizard-message" role="status" aria-live="polite">${esc(message)}</p><div id="wizard-close-warning" hidden><p>本步有未保存的修改，退出后会丢弃。</p><button class="button" data-wizard="keep">继续编辑</button><button class="button" data-wizard="discard-close">放弃本步修改并退出</button></div></div><div class="wizard-footer">${w.step>0?'<button class="button" data-wizard="back">上一步</button>':'<button class="button" data-wizard="close">稍后再配</button>'}${w.step<2?`<button class="button" data-wizard="skip">${image?'不保存本步，稍后配置图片':'不保存本步，下一步'}</button><button class="button primary" data-wizard="next">保存并下一步</button>`:'<button class="button primary" data-wizard="finish">完成</button>'}</div>`;
  // A typed credential stays only in this open wizard; it is never persisted as a draft.
  const key=$('#wizard-key');if(key)key.value=d.api_key;
  $('#model-wizard').scrollTop=0;
}
function captureWizard() {
  const w=modelWizard;if(!w || w.step===2)return;
  const d=w.step===1?w.drafts.image:w.drafts.text;
  d.model=$('#wizard-model').value;
  if($('#wizard-base'))d.base_url=$('#wizard-base').value;
  if($('#wizard-key'))d.api_key=$('#wizard-key').value;
  if(w.step===1){d.use_shared=$('#wizard-image-shared').checked;d.cover_model=$('#wizard-cover').value;d.illustration_model=$('#wizard-illustration').value;d.references=$('#wizard-references').value;}
}
async function saveWizardStep() {
  captureWizard();const w=modelWizard;
  let values;
  if(w.step===0)values={connection:{...w.drafts.text,mode:'api'}};
  else {const d=w.drafts.image;values={image:{model:d.model,cover_model:d.cover_model,illustration_model:d.illustration_model,references:d.references,use_shared:d.use_shared,...(d.use_shared?{}:{base_url:d.base_url,api_key:d.api_key})}};}
  const saved=await api('/model-settings',values);w.saved=saved;
  w.drafts[w.step===0?'text':'image']=wizardDrafts(saved)[w.step===0?'text':'image'];w.dirty=false;
  renderModelSetup(saved.setup);
  if($('#wizard-key'))$('#wizard-key').value='';
  // Reconcile a clean settings page without losing unrelated fields or typed edits.
  if($('#model-settings-panel')){
    updateModelStatus(saved);
    $('#ai-base').value=saved.connection.base_url;$('#ai-model').value=saved.connection.model;$('#ai-mode').value=saved.mode;
    $('#ai-api-fields').hidden=saved.mode==='program';$('#ai-program-fields').hidden=saved.mode!=='program';
    $('#ai-key').value='';$('#ai-key').placeholder=saved.connection.key_set?'已配置，留空保留':'填写模型调用密钥';
    $('#model-image-shared').checked=saved.image.use_shared;$('#model-image-independent').hidden=saved.image.use_shared;
    $('#model-image-base').value=saved.image.base_url;$('#model-image-default').value=saved.image.model;$('#model-image-key').value='';
    $('#model-image-key').placeholder=saved.image.key_set?'已配置，留空保留':'填写图片服务密钥';
    $('#model-image-references').value=saved.image.references;
    for(const task of saved.image_tasks)$(`[data-model-task="${task.id}"]`).value=task.model;
  }
}
function closeModelWizard(discard=false) {
  if(!modelWizard || modelWizard.busy)return;
  captureWizard();
  modelWizard.dirty=JSON.stringify(modelWizard.drafts)!==JSON.stringify(wizardDrafts(modelWizard.saved));
  if(modelWizard.dirty && !discard){$('#wizard-close-warning').hidden=false;$('#wizard-close-warning').scrollIntoView({block:'nearest'});return;}
  $('#model-wizard').close();$('#model-wizard').innerHTML='';modelWizard=null;
}
async function wizardAction(action) {
  const w=modelWizard;if(!w || w.busy)return;
  if(action==='close'||action==='finish'){closeModelWizard();return;}
  if(action==='discard-close'){closeModelWizard(true);return;}
  if(action==='keep'){$('#wizard-close-warning').hidden=true;return;}
  if(action==='back'){captureWizard();w.step--;drawModelWizard();return;}
  w.busy=true;$('#model-wizard').querySelectorAll('button,input,select').forEach(el=>el.disabled=true);
  try {
    if(action==='catalog'){
      captureWizard();const image=w.step===1,d=image?w.drafts.image:w.drafts.text;
      const shared=image&&d.use_shared;
      const result=await api(image&&!shared?'/illustrations/models':'/ai/models',shared?{}:{base_url:d.base_url,api_key:d.api_key,...(image?{connection:'independent'}:{})});
      $('#wizard-catalog-choice').innerHTML='<option value="">请选择支持本步能力的模型</option>'+result.models.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('');
      $('#wizard-message').textContent=`已读取 ${result.models.length} 个模型；选择后仍需点击保存。`;
    } else if(action==='skip') {
      w.drafts[w.step===0?'text':'image']=wizardDrafts(w.saved)[w.step===0?'text':'image'];w.dirty=false;w.step++;
      if(w.step===2)w.saved=await api('/model-settings');drawModelWizard();
    } else {
      await saveWizardStep();
      if(action==='test') {
        $('#wizard-message').textContent='正在自测，请等待结果…';
        const result=await api('/model-settings/test',{task:w.step===0?'chat':'cover'});
        w.saved=await api('/model-settings');drawModelWizard(result.model+'：'+result.note);
      } else {
        if(w.step===0 && !w.saved.setup.configured)throw Error(w.saved.setup.missing.map(i=>i.label).join('；')+'。也可选择稍后配置。');
        if(w.step===1 && w.saved.image_tasks.some(t=>!t.configured))throw Error('生图配置还不完整，请检查接口、密钥和模型名称；也可选择稍后配置图片。');
        w.step++;drawModelWizard();
      }
    }
  } catch(error){
    $('#wizard-message').textContent=error.message;
    if(action==='test') {try{w.saved=await api('/model-settings');}catch(_){}}
  } finally {w.busy=false;$('#model-wizard').querySelectorAll('button,input,select').forEach(el=>el.disabled=false);}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-wizard]');if(!button)return;
  if(button.dataset.wizard==='open'){void openModelWizard();return;}
  void wizardAction(button.dataset.wizard);
});
