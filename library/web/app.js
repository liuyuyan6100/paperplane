'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token = '', timer, sequence = 0, selected = '', inTrash = false, uploadBusy = false;
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Library-Token':token},body:JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '读取失败');
  return value;
}
async function listing() {
  const current = ++sequence;
  try {
    const result = await api('/api/items?q='+encodeURIComponent($('#query').value)+'&source='+encodeURIComponent($('#source').value)+'&trash='+(inTrash?'1':'0'));
    if (current !== sequence) return;
    const source = $('#source').value;
    $('#source').innerHTML = '<option value="">全部来源</option>' + [...new Set([...result.sources.map(s=>s.name),'新资料'])].map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');
    $('#source').value = source;
    $('#source-action').hidden = !source;
    $('#source-action').textContent = inTrash ? '恢复整个来源' : '移除整个来源';
    $('#source-help').textContent = source ? (inTrash ? '恢复整组移除的资料；单独移除的资料需单独恢复。登记目录恢复后继续支持手动检查更新。' : '整组移入回收站，并暂停该来源的更新；原文件保留。') : '选择一个来源，可整组移入回收站；原文件保留。';
    $('#trash').textContent = inTrash ? '返回资料库' : `回收站（${result.trash_count}）`;
    $('#scan').hidden = inTrash;
    $('#status').textContent = `${inTrash?'回收站':'共'} ${result.total} 份资料 · ${(result.bytes/1048576).toFixed(1)} MiB · 当前显示 ${result.items.length} 份` + (inTrash ? ' · 可恢复，副本仍占用磁盘空间' : '');
    $('#items').innerHTML = result.items.map(i=>`<article class="item"><h3>${esc(i.name)}</h3><small>${esc(i.source)} · v${i.version} · ${(i.size/1024).toFixed(1)} KiB</small><small>${esc(i.relative_path)}</small>${inTrash?`<button data-restore="${i.id}">恢复资料</button>`:`<button data-id="${i.id}">查看资料</button><button class="secondary-button" data-delete="${i.id}">移入回收站</button>`}</article>`).join('') || '<p>没有找到资料。</p>';
  } catch (e) { $('#status').textContent = e.message; }
}
async function detail(id, version) {
  try {
    const i = await api('/api/items/'+id+(version?'?version='+version:''));
    selected=id;
    $('#title').textContent=i.name;
    $('#meta').textContent=`${i.source} · v${i.version} · 资料编号 ${i.id}`;
    $('#download').href='/api/items/'+id+'/content?version='+i.version;
    $('#version').innerHTML=i.versions.map(v=>`<option value="${v.version}">v${v.version}</option>`).join('');
    $('#version').value=String(i.version);
    $('#text').textContent=i.text || '此文件可下载查看。';
    if (!$('#detail').open) $('#detail').showModal();
  } catch(e) { $('#status').textContent=e.message; }
}
async function changeItem(id, restore=false) {
  await api('/api/items/'+id+(restore?'/restore':'/archive'),{});
  await listing();
  $('#status').textContent += restore ? ' · 已恢复资料' : ' · 已移入回收站，原文件保留；检查更新不会重新加入';
}
$('#query').oninput=()=>{clearTimeout(timer);timer=setTimeout(listing,200);};
$('#source').onchange=listing;
$('#items').onclick=async e=>{
  const b=e.target.closest('button');
  if (!b) return;
  b.disabled=true;
  try {
    if(b.dataset.id) await detail(b.dataset.id);
    else if(b.dataset.delete) await changeItem(b.dataset.delete);
    else if(b.dataset.restore) await changeItem(b.dataset.restore,true);
  } catch(e) { $('#status').textContent=e.message; }
  finally { b.disabled=false; }
};
$('#close').onclick=()=>$('#detail').close();
$('#version').onchange=()=>detail(selected,$('#version').value);
$('#detail-delete').onclick=async()=>{try{await changeItem(selected);$('#detail').close();}catch(e){$('#meta').textContent=e.message;}};
$('#trash').onclick=()=>{inTrash=!inTrash;listing();};
$('#source-action').onclick=async()=>{
  const source=$('#source').value, restore=inTrash;
  if(!source) return;
  $('#source-action').disabled=true;
  try {
    const r=await api('/api/sources/'+(restore?'restore':'archive'),{name:source});
    await listing();
    $('#status').textContent+=` · ${restore?'恢复':'移除'} ${r.changed} 份资料，原文件保留`;
  }catch(e){$('#status').textContent=e.message;}
  finally{$('#source-action').disabled=false;}
};
$('#scan').onclick=async()=>{
  $('#scan').disabled=true;
  try{
    const r=await api('/api/scan',{});
    await listing();
    $('#status').textContent+=` · 更新 ${r.updated} 份`+(r.skipped_deleted?` · 跳过回收站资料 ${r.skipped_deleted} 份`:'')+(r.errors.length?' · '+r.errors.join('；'):'');
  }catch(e){$('#status').textContent=e.message;}
  finally{$('#scan').disabled=false;}
};
$('#add-folder').onclick=()=>$('#folder').showModal();
$('#folder-close').onclick=()=>{if(!uploadBusy)$('#folder').close();};
$('#folder').oncancel=e=>{if(uploadBusy)e.preventDefault();};
$('#register-form').onsubmit=async e=>{
  e.preventDefault();
  if(uploadBusy)return;
  uploadBusy=true;
  $('#register').disabled=true;
  $('#folder-status').textContent='正在登记并导入文件夹…';
  try{
    const r=await api('/api/sources/register',{name:$('#folder-name').value,path:$('#folder-path').value});
    await listing();
    $('#folder-status').textContent=`已登记，检查 ${r.scan.seen} 份，更新 ${r.scan.updated} 份。以后点击“检查来源更新”即可。`+(r.scan.errors.length?' '+r.scan.errors.join('；'):'');
  }catch(e){$('#folder-status').textContent=e.message;}
  finally{uploadBusy=false;$('#register').disabled=false;}
};
function fileContent(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(new Error('文件读取失败'));r.readAsDataURL(file);});}
async function uploadFiles(input, folder=false){
  if(uploadBusy || !input.files.length)return;
  uploadBusy=true;
  const files=[...input.files], status=folder?$('#folder-status'):$('#status');
  let done=0, skipped=0, failed=0, source='新资料';
  const errors=[];
  try{
    if(folder){
      const root=files[0].webkitRelativePath.split('/')[0];
      source=(await api('/api/folder-import',{name:root})).source;
    }
    for(let n=0;n<files.length;n++){
      const file=files[n], relative=file.webkitRelativePath;
      if(folder && (file.size>32*1048576 || !/\.(md|txt|html?|pdf|png|jpe?g|webp|gif|svg|csv|docx|pptx|xlsx)$/i.test(file.name) || relative.split('/').some(p=>p.startsWith('.') || p.startsWith('_') || ['node_modules','logs','backups'].includes(p)))){skipped++;continue;}
      status.textContent=`正在导入 ${n+1}/${files.length}：${relative||file.name}`;
      try{
        if(file.size>32*1048576)throw new Error('单个文件上限为 32 MiB');
        await api('/api/upload',{name:file.name,source,...(folder?{relative_path:relative}:{}),content:await fileContent(file)});
        done++;
      }catch(e){failed++;if(errors.length<5)errors.push(file.name+'：'+e.message);}
    }
    await listing();
    status.textContent=`导入完成：成功 ${done} 份，跳过 ${skipped} 份，失败 ${failed} 份。`+(folder?' 子目录结构已保留。':'')+(errors.length?' '+errors.join('；'):'');
  }catch(e){status.textContent=e.message;}
  finally{input.value='';uploadBusy=false;}
}
$('#upload').onchange=e=>uploadFiles(e.target);
$('#folder-upload').onchange=e=>uploadFiles(e.target,true);
window.addEventListener('beforeunload',e=>{if(uploadBusy){e.preventDefault();e.returnValue='';}});
api('/api/session').then(s=>{token=s.token;return listing();}).catch(e=>{$('#status').textContent=e.message;});
