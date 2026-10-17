"use strict";
let performanceData=null, performanceReview=null, performanceForm=null, performanceTimer=null, performanceSequence=0;
const performanceLabels={reads:'阅读',likes:'点赞',comments:'评论',favorites:'收藏',shares:'分享'};
const performanceStates={queued:'排队中',running:'复盘中',ready:'建议待核对',failed:'生成失败',interrupted:'已中断'};
const performanceSources={manual:'手动录入',devto:'DEV 接口',legacy:'历史数据'};
const performanceRequest=()=>crypto.randomUUID().replaceAll('-','');
// Confirmation in the shared memory dialog must be reflected when returning here.
document.addEventListener('DOMContentLoaded',()=>{
  document.querySelector('#modal')?.addEventListener('close',()=>{
    if(location.hash.startsWith('#analytics/')&&!document.querySelector('#modal').open)void drawPerformance();
  });
});
function performanceMetrics(sample){
  return Object.entries(performanceLabels).map(([key,label])=>`<span class="tag">${label} ${sample?.metrics[key]??'未取得'}</span>`).join(' ');
}
function performanceSampleHtml(sample){
  return `<article class="panel panel-body spaced"><p>${performanceMetrics(sample)}</p><p>统计截止：${esc(new Date(sample.observed_at).toLocaleString())} · 自发表起累计</p><p class="tip">${esc(performanceSources[sample.source])} · ${esc(sample.source_note)}<br>录入于 ${esc(new Date(sample.created_at).toLocaleString())}</p>${sample.note?`<p class="workflow-preserve">${esc(sample.note)}</p>`:''}</article>`;
}
async function drawPerformance(){
  clearTimeout(performanceTimer);
  const seq=++performanceSequence,hash=location.hash,id=hash.split('/')[1];
  try{
    if(id){
      const r=await api('/performance/reviews/'+id);
      if(seq!==performanceSequence||location.hash!==hash)return;
      performanceReview=r;
      const sample=r.context.sample;
      $('#main').innerHTML=heading('发表后的复盘',`${sample.publication.title} · ${platformNames()[sample.channel_id]||sample.channel_id}`,'<a class="button" href="#analytics">← 数据复盘</a>')+
        `<section class="panel panel-body"><span class="tag">${esc(performanceStates[r.status])}</span><p>${esc(r.error)}</p><p>画像：${esc(r.context.profile.name)} · v${r.context.profile.version}</p><p class="tip">本次使用发表稿 v${esc(sample.publication.version)}、保存时的画像与数据。单篇累计数据只能提供观察和待验证建议。</p>${modelProvenanceHtml(r.model_provenance)}${memorySnapshotHtml(r.context.profile)}${r.data_changed||r.profile_changed?'<p class="notice">数据或画像已有更新；这里保留的是生成时的旧快照。</p>':''}${['failed','interrupted'].includes(r.status)?'<button class="button" data-action="performance-retry">按原快照重试</button>':''}</section>`+
        `<details class="spaced"><summary>查看本次数据与来源</summary>${performanceSampleHtml(sample)}<details><summary>发表时的文章</summary><pre class="chat-preview">${esc(sample.publication.body||'')}</pre></details></details>`+
        (r.status==='ready'?`<section class="panel panel-body spaced"><h2>复盘摘要</h2><p class="workflow-preserve">${esc(r.result.summary)}</p><h3>数据局限</h3><ul>${r.result.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h3>观察</h3>${r.result.observations.map(x=>`<p>${esc(x.content)}</p>${performanceEvidence(x,r)}`).join('')||'<p>当前数据不足以形成具体观察。</p>'}</section><section class="spaced"><h2>可用于下一篇的候选经验</h2><p class="tip">先核对内容与依据，存为候选后逐条确认生效。修改、停用和来源记录沿用画像长期记忆。</p>${r.result.candidates.map(c=>{const m=r.candidate_memories[c.id];return `<article class="panel panel-body spaced"><p class="workflow-preserve"><strong>${esc(c.content)}</strong></p><p>${esc(c.reason)}</p>${performanceEvidence(c,r)}<p class="tip">适用平台：${esc(platformNames()[sample.channel_id]||sample.channel_id)} · 主题：${esc(c.keywords||'所有主题')}</p>${m?`<p>画像经验：${esc(memoryStates[m.status])}</p><button class="button" data-action="performance-memories">查看或确认画像经验</button>`:`<button class="button primary" data-action="performance-propose-open" data-id="${esc(c.id)}">核对并存为候选经验</button>`}</article>`;}).join('')||'<p class="empty">这次没有足够依据形成可复用经验。</p>'}</section>`:'<p class="spaced">生成任务会保留记录；可以离开此页，稍后回来查看。</p>');
      if(['queued','running'].includes(r.status))performanceTimer=setTimeout(()=>{if(location.hash===hash&&!$('#modal').open)void drawPerformance();},1800);
      return;
    }
    const data=await api('/performance');
    if(seq!==performanceSequence||location.hash!==hash)return;
    performanceData=data;performanceReview=null;
    $('#main').innerHTML=heading('数据复盘','记录发表表现，核对建议，把适用的经验带进下一次创作。','<button class="button" data-action="performance-refresh">刷新数据</button>')+
      '<section class="panel panel-body"><p>先录入平台后台的累计指标和统计截止时间，再生成复盘。未取得的指标留空；0 表示已经确认数值为零。</p><p class="tip">DEV 可主动同步接口数据，其他平台先手动录入。各平台统计口径不同，当前按单篇、单平台分析。</p></section>'+
      `<section class="panel table-wrap spaced"><div class="panel-head"><h2>已发表内容</h2><small>${data.publications.length} 条渠道记录</small></div>${data.publications.length?`<table><thead><tr><th>文章 / 平台</th><th>最新录入的数据</th><th>统计截止 / 来源</th><th>操作</th></tr></thead><tbody>${data.publications.map(j=>`<tr><td class="title-cell"><b>${esc(j.title)}</b><small>${esc(j.channel_name)}</small></td><td>${performanceMetrics(j.sample)}</td><td>${j.sample?`${esc(new Date(j.sample.observed_at).toLocaleString())}<br><small>${esc(performanceSources[j.sample.source])}</small>`:(j.legacy_unmigrated?'旧数据需核对截止时间':'未采集')}</td><td><div class="actions"><button class="button small" data-action="performance-sample-open" data-id="${j.id}">录入 / 历史</button><button class="button small primary" data-action="performance-create-open" data-id="${j.id}" ${j.sample?'':'disabled'}>生成复盘</button>${j.channel_id==='devto'&&j.external_id?`<button class="button small" data-action="performance-sync" data-id="${j.id}">同步 DEV</button>`:''}</div></td></tr>`).join('')}</tbody></table>`:empty('还没有已发表记录','在文章的发布记录中确认实际发表结果后，这里就能关联数据。')}</section>`+
      `<section class="panel panel-body spaced"><h2>复盘记录</h2>${data.reviews.map(r=>`<p><a class="text-link" href="#analytics/${r.id}">${esc(r.title)}</a> <span class="tag">${esc(performanceStates[r.status])}</span> <small>${esc(fmt(r.created_at))}</small></p>`).join('')||'<p class="tip">录入数据后，点击“生成复盘”开始。</p>'}</section>`;
  }catch(error){if(seq===performanceSequence&&location.hash===hash)$('#main').innerHTML=heading('数据复盘','')+`<p class="error-inline">${esc(error.message)}</p><button class="button" data-action="performance-refresh">重试</button>`;}
}
function performanceEvidence(item,review){return `<details><summary>依据的数据</summary>${item.evidence_ids.map(k=>`<p class="tip">${esc(review.context.evidence[k])}</p>`).join('')}</details>`;}
async function performanceSampleForm(id){
  const hash=location.hash,data=await api('/performance/jobs/'+id),latest=data.samples[0];
  if(location.hash!==hash)return;
  performanceForm={id,previous_id:latest?.id||null,request_id:performanceRequest()};
  const localTime=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
  modal('录入发表数据',`<p>填写平台后台“自发表起累计”的数值。无法取得的项目留空；每次保存为新快照，历史继续保留。</p><div class="form-grid">${Object.entries(performanceLabels).map(([k,v])=>`<label>${v}<input id="performance-${k}" type="number" min="0" max="1000000000" step="1" placeholder="未取得"></label>`).join('')}<label>统计截止时间<input id="performance-observed" type="datetime-local" value="${localTime}"></label></div><label>数据来源说明<input id="performance-source" maxlength="300" placeholder="例如：公众号后台文章分析，自发表起累计"></label><label>读者反馈与复盘笔记<textarea id="performance-note" maxlength="4000" rows="4" placeholder="记录具体反馈，注明是读者原话还是自己的观察。"></textarea></label><details class="spaced"><summary>历史数据（${data.samples.length} 次，最近录入在前）</summary>${data.samples.map(performanceSampleHtml).join('')||'<p>尚无记录。</p>'}</details>`, '<button class="button" data-action="close">取消</button><button class="button primary" data-action="performance-sample-save">保存数据快照</button>');
}
async function handlePerformanceAction(button){
  const action=button.dataset.action;
  if(!action?.startsWith('performance-'))return false;
  if(action==='performance-refresh'){await drawPerformance();return true;}
  if(action==='performance-sample-open'){await performanceSampleForm(button.dataset.id);return true;}
  if(action==='performance-create-open'){
    const j=performanceData.publications.find(j=>j.id===button.dataset.id);
    if(!j?.sample)throw new Error('请先录入数据');
    const available=await api('/profiles');
    performanceForm={job:j,request_id:performanceRequest()};
    modal('生成发表复盘',`<p>${esc(j.title)} · ${esc(j.channel_name)}</p>${performanceSampleHtml(j.sample)}<label>使用画像<select id="performance-profile" ${j.profile_id?'disabled':''}>${available.profiles.map(p=>`<option value="${p.id}" ${p.id===j.profile_id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label><p class="tip">使用发表稿和当前画像目标分析这份数据。复盘通过已有审稿模型生成，结果保留为待核对建议。</p><div id="performance-planned"></div>`, '<button class="button" data-action="close">取消</button><button class="button primary" data-action="performance-create">开始复盘</button>');
    void renderPlannedModel('#performance-planned','review');return true;
  }
  if(action==='performance-propose-open'){
    const r=performanceReview,c=r.result.candidates.find(c=>c.id===button.dataset.id);
    performanceForm={candidate:c,review:r};
    modal('核对候选经验',`<p>将关联到 ${esc(r.context.profile.name)}，适用 ${esc(platformNames()[r.context.sample.channel_id]||r.context.sample.channel_id)}。</p><p class="workflow-preserve">${esc(c.content)}</p><p>${esc(c.reason)}</p>${performanceEvidence(c,r)}<ul>${r.result.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>${r.data_changed||r.profile_changed?'<label class="check-label"><input id="performance-ack" type="checkbox">我已核对旧数据与画像，仍保存这条候选</label>':''}<p class="tip">保存后在画像经验中逐条确认生效，也可以先修改文字。</p>`, '<button class="button" data-action="close">返回</button><button class="button primary" data-action="performance-propose">存为待确认经验</button>');return true;
  }
  if(action==='performance-memories'){await openMemories(performanceReview.profile_id);return true;}
  button.disabled=true;
  try{
    if(action==='performance-sample-save'){
      const f=performanceForm,values={request_id:f.request_id,previous_id:f.previous_id,source_note:$('#performance-source').value,note:$('#performance-note').value};
      const time=$('#performance-observed').value;if(!time)throw new Error('请填写统计截止时间');values.observed_at=new Date(time).toISOString();
      for(const k of Object.keys(performanceLabels)){const v=$('#performance-'+k).value;values[k]=v===''?null:Number(v);}
      await api('/performance/jobs/'+f.id,values);closeModal();await refresh(false);await drawPerformance();toast('数据快照已保存');
    }else if(action==='performance-create'){
      const f=performanceForm,profile_id=$('#performance-profile').value;if(!profile_id)throw new Error('请先在画像管理中创建画像');
      const r=await api('/performance/reviews',{request_id:f.request_id,job_id:f.job.id,sample_id:f.job.sample.id,profile_id});closeModal();location.hash='#analytics/'+r.id;
    }else if(action==='performance-retry'){
      await api('/performance/reviews/'+performanceReview.id+'/retry',{});await drawPerformance();
    }else if(action==='performance-propose'){
      const {review:r,candidate:c}=performanceForm;
      await api('/performance/reviews/'+r.id+'/propose',{candidate_id:c.id,ack_changed:$('#performance-ack')?.checked||false});
      closeModal();await drawPerformance();await openMemories(r.profile_id);
    }else if(action==='performance-sync'){
      const result=await api('/jobs/'+button.dataset.id+'/sync',{});await refresh(false);await drawPerformance();toast(result.metrics_saved?'DEV 数据已同步并保留快照':'平台状态已同步，本次未返回可用指标');
    }
  }finally{button.disabled=false;}
  return true;
}
