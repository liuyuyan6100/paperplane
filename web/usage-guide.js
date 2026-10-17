"use strict";
// Progress records what the reader marked, never a platform delivery result.
const usageSteps = [
  {title:'准备文字与生图模型', short:'配置模型', lead:'想让 AI 帮你写文章、做封面，先连接模型。',
   items:['打开模型配置向导，填写文字接口、API Key 和模型，保存后可主动自测。','继续配置生图模型，用于公众号封面和正文插图。它可以使用独立接口。','已经配置过可以直接继续；自己写文章、上传图片，也可以跳过模型配置。'],
   tip:'自测和生成可能消耗模型额度，打开引导不会调用模型。', actions:[['models','打开模型配置向导']]},
  {title:'连接你的微信公众号', short:'连接公众号', lead:'先确认文章要送到哪个公众号，再检查草稿箱权限。',
   items:['进入公众号设置，选择「自动送入公众号草稿箱」，填入这个公众号的 AppID 与 AppSecret。已有配置可以保留。','点击「保存并测试公众号连接」，按返回的错误处理权限或 IP 白名单；通过表示可读取草稿权限。','进入渠道管理，找到「微信公众号」并启用。连接检查通过与真实送稿成功是两件事。'],
   tip:'没有接口权限时，可选择导出发布包、到微信后台手动上传；后面的自动送草稿步骤不适用于手动方式。', actions:[['wechat','去配置公众号'],['platforms','查看渠道管理']]},
  {title:'写好第一篇文章', short:'准备正文', lead:'先保存一篇母稿，后续再制作适合公众号的图文。',
   items:['在内容库点「新建内容」，填写标题并保存。已有文章可直接打开，不必重复新建。','可以自己写正文；想让 AI 写作，在文章编辑器选「生成提纲 / 写正文」，填写素材和要求。','在创作任务中查看提纲，修改后点「确认提纲并生成正文」；审阅正文，再点「采用并保存到原文章」。'],
   tip:'采用前核对事实与引用。已有完整正文可直接进入下一步。', actions:[['content','打开内容库'],['workflows','查看创作任务']]},
  {title:'制作公众号图文', short:'制作图文', lead:'公众号使用已采用的原文章，在同一篇文章中补齐封面和正文配图。',
   items:['从对话任务卡点「编辑公众号图文」，或在内容库打开刚才采用的文章。公众号无需另建平台改编。','在对话卡点「安排配图规划」，或打开配图工作台选择这篇文章；审阅规划后生成候选图并采用。也可以在文章编辑器上传已有图片。','回到文章编辑器检查标题、摘要、作者、封面和正文插图，修改后保存；AI 生成图片不能冒充真实操作截图。'],
   tip:'配图采用与文章保存有版本检查。候选图片生成成功不等于已经采用，也不代表已经送稿。', actions:[['content','打开文章编辑图文'],['illustrations','打开配图工作台']]},
  {title:'审阅并送入公众号草稿箱', short:'检查送稿', lead:'确认图文内容后，让纸飞机检查并送草稿。',
   items:['在文章编辑器核对图文并保存，点击「检查并送入公众号草稿箱」。缺少摘要、作者或图片等信息时，先审阅准备建议或补齐信息；通过检查后进入草稿队列。','打开发布队列查看这篇文章：缺图或其他问题按提示处理；成功显示平台草稿及草稿编号。','如果显示「结果待核对」，先打开任务重新核对微信草稿，或到微信后台核实，不要直接重发。'],
   tip:'通过检查并创建的任务由工作台执行。已有草稿或结果待核对时先查看原任务，不要重复创建。', actions:[['content','打开文章检查送稿'],['queue','查看发布队列']]},
  {title:'在微信后台确认发表', short:'确认发表', lead:'草稿已经准备好，最后的公开发表由你在微信后台完成。',
   items:['从渠道管理打开微信公众号后台，找到同一篇草稿，预览并核对账号、标题、正文、图片和链接。','按微信后台提供的流程确认公开发表。「平台草稿」只表示已送草稿，不代表读者已经可见。','发表后回到纸飞机发布队列查看记录；未自动记录时，在任务中「确认已发布」并补充公开文章地址。'],
   tip:'完成下方学习进度只表示你已了解本步骤，不会发表文章，也不会修改发布任务的状态。', actions:[['platforms','打开渠道管理'],['queue','查看与登记发表结果']]}
];
let usageGuide=null, usageGuideBusy=false, usageGuideLoading=null, usageGuideError='', usageFocus=null;
async function loadUsageGuide() {
  if(usageGuideLoading)return usageGuideLoading;
  usageGuideLoading=api('/usage-guide').then(s=>{usageGuide=s;usageGuideError='';}).catch(e=>{usageGuideError=e.message;}).finally(()=>{usageGuideLoading=null;renderUsageDock();});
  return usageGuideLoading;
}
function usageGuideEntry(){return '<section class="usage-entry"><div><strong>第一次发公众号文章？</strong><p>从配置、写作到送草稿，跟着步骤完成。</p></div><button class="button" data-guide="open">打开使用引导 →</button></section>';}
function renderUsageGuide() {
  if(view!=='guide')return;
  if(!usageGuide){$('#main').innerHTML=heading('使用引导','一步一步，完成第一篇公众号文章')+`<p role="status">${esc(usageGuideError || '正在读取学习进度…')}</p>${usageGuideError?'<button class="button" data-guide="retry">重新读取</button>':''}`;return;}
  const s=usageGuide,step=usageSteps[s.step],finished=s.completed.length===usageSteps.length;
  $('#main').innerHTML=heading('使用引导','第一次发公众号文章', '<button class="button" data-guide="reset">重新开始引导</button>')+
    `<section class="usage-overview"><div><span class="tag blue">公众号 · 6 步</span><h2>从一篇想法，到公众号里的文章</h2><p>先送入草稿箱，最后到微信后台确认发表。</p></div><div><b>${s.completed.length} / 6</b><small>手动标记的学习进度</small></div></section>
    ${finished?'<p class="notice blue" role="status">已完成全部学习步骤。实际送稿与发表状态，请在发布队列查看。仍可点击任一步重看。</p>':''}
    <div class="usage-layout"><nav class="usage-step-list" aria-label="使用引导步骤">${usageSteps.map((t,i)=>`<button data-guide="step" data-step="${i}" ${i===s.step?'aria-current="step"':''}><span>${s.completed.includes(i)?'✓':i+1}</span><div>${esc(t.short)}<small>${s.completed.includes(i)?'已标记完成':'可随时查看'}</small></div></button>`).join('')}</nav>
    <section class="panel usage-detail"><span class="muted">第 ${s.step+1} 步 / 6</span><h2 tabindex="-1" id="usage-step-title">${esc(step.title)}</h2><p>${esc(step.lead)}</p><ol>${step.items.map(t=>`<li>${esc(t)}</li>`).join('')}</ol><p class="notice blue">${esc(step.tip)}</p><div class="actions">${step.actions.map(([id,label])=>`<button class="button" data-guide="go" data-destination="${id}">${esc(label)} →</button>`).join('')}</div>
    <footer><button class="button" data-guide="previous" ${s.step===0?'disabled':''}>上一步</button><button class="button primary" data-guide="complete">${s.step===5?'我已了解，完成引导':'我已完成 / 已了解，下一步'}</button></footer><p class="tip">步骤可以跳着看。这里只保存学习进度，不会自动生成、送稿或公开发表。</p></section></div><p id="usage-guide-status" role="status">${esc(usageGuideError)}</p>`;
  $('#main').querySelectorAll('[data-guide]').forEach(b=>{if(usageGuideBusy)b.disabled=true;});
}
function renderUsageDock(){
  let dock=$('#usage-guide-dock');if(!dock){dock=document.createElement('aside');dock.id='usage-guide-dock';dock.setAttribute('aria-label','当前使用引导');document.body.append(dock);}
  const show=usageGuide?.active && view!=='guide';dock.hidden=!show;document.body.classList.toggle('usage-guide-active',!!show);
  if(!show)return;
  const step=usageSteps[usageGuide.step];dock.innerHTML=`<div><strong>使用引导 · ${usageGuide.step+1}/6 ${esc(step.short)}</strong><button class="close" data-guide="pause" aria-label="收起引导，保留进度">×</button></div><p>${esc(step.lead)}</p><div class="actions"><button class="button small" data-guide="open">查看本步说明</button><button class="button small" data-guide="complete">${usageGuide.step===5?'已了解，完成':'已了解，下一步'}</button></div>${usageGuideError?`<p role="alert">${esc(usageGuideError)}</p>`:''}`;
  dock.querySelectorAll('button').forEach(b=>b.disabled=usageGuideBusy);
}
function drawUsageGuide(){renderUsageDock();renderUsageGuide();if(!usageGuide&&!usageGuideError)void loadUsageGuide().then(renderUsageGuide);}
let usageSettingsBaseline=null;
function captureUsageSettings(){return Object.fromEntries($$('#main input,#main textarea,#main select').filter(e=>e.id&&!e.closest('#model-settings-panel')).map(e=>[e.id,['checkbox','radio'].includes(e.type)?e.checked:e.value]));}
function rememberUsageSettings(saved=captureUsageSettings()){usageSettingsBaseline=saved;}
function guideCanNavigate(){
  const current=captureUsageSettings();
  const changedSettings=view==='settings' && (wizardPageDirty() || usageSettingsBaseline && Object.keys(current).some(k=>current[k]!==usageSettingsBaseline[k]));
  if(dirty||profileDirty||repurposeDirty||illustrationDirty||sharedLibraryUploading||changedSettings){toast('请先保存当前修改或完成导入，再切换引导步骤');return false;}
  if($('dialog[open]')){toast('请先完成或关闭当前操作，再切换引导步骤');return false;}
  return true;
}
async function changeUsageGuide(action,step){
  if(usageGuideBusy)return false;usageGuideBusy=true;usageGuideError='';renderUsageDock();
  try{usageGuide=await api('/usage-guide',{action,...(step===undefined?{}:{step})});return true;}
  catch(e){usageGuideError='学习进度未保存：'+e.message;toast(usageGuideError);return false;}
  finally{usageGuideBusy=false;renderUsageGuide();renderUsageDock();}
}
function focusUsageDestination(){
  if(!usageFocus)return;
  const {hash,selector}=usageFocus;if(location.hash!==hash || view!==hash.slice(1))return;
  const target=$(selector);if(!target)return;usageFocus=null;
  const section=target.closest('section') || target;section.classList.add('usage-highlight');section.scrollIntoView({block:'center'});target.setAttribute('tabindex','-1');target.focus({preventScroll:true});setTimeout(()=>section.classList.remove('usage-highlight'),4500);
}
new MutationObserver(()=>{focusUsageDestination();renderUsageDock();}).observe(document.querySelector('#main'),{childList:true,subtree:true});
document.addEventListener('DOMContentLoaded',()=>{void loadUsageGuide();});
window.addEventListener('hashchange',()=>{queueMicrotask(()=>{if(usageFocus?.hash!==location.hash)usageFocus=null;renderUsageDock();focusUsageDestination();});});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-guide]');if(!button)return;event.preventDefault();if(usageGuideBusy)return;
  const action=button.dataset.guide;
  if(action==='pause'){await changeUsageGuide('pause');return;}
  if(action==='retry'){await loadUsageGuide();renderUsageGuide();return;}
  if(!guideCanNavigate())return;
  if(action==='open'){
    if(!usageGuide)await loadUsageGuide();
    location.hash='#guide';if(view==='guide')drawUsageGuide();return;
  }
  if(!usageGuide){toast('请先读取引导进度');return;}
  if(action==='reset'){if(await changeUsageGuide('reset'))$('#usage-step-title')?.focus();return;}
  if(action==='step'||action==='previous'||action==='complete'){
    const step=action==='step'?Number(button.dataset.step):action==='previous'?usageGuide.step-1:usageGuide.step;
    if(await changeUsageGuide(action==='complete'?'complete':'goto',step)){location.hash='#guide';$('#usage-step-title')?.focus();}return;
  }
  if(action==='go'){
    if(!await changeUsageGuide('start'))return;
    const dest=button.dataset.destination;
    if(dest==='models'){await openModelWizard();return;}
    const targets={wechat:['#settings','#wechat-mode'],platforms:['#platforms','[data-managed-channel="wechat"]'],content:['#content','[data-action="new"]'],workflows:['#workflows','#main h1'],repurpose:['#repurpose','#repurpose-source'],illustrations:['#illustrations','#main h1'],queue:['#queue','#main h1']};
    if(!targets[dest])return;
    const [hash,selector]=targets[dest];usageFocus={hash,selector};location.hash=hash;focusUsageDestination();
  }
});
