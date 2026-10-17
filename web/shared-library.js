let sharedLibrarySequence = 0;
let sharedLibraryUploading = false;
async function drawSharedLibrary() {
  const sequence = ++sharedLibrarySequence;
  const route = location.hash;
  $('#main').innerHTML = heading('共享资料库', '与本机 Easel 共用笔记、图片和文档。',
    '<button class="button primary" id="library-folder-button">导入文件夹</button><button class="button" id="library-file-button">添加文件</button><button class="button" id="open-feishu">飞书在线资料</button><a class="button" href="http://127.0.0.1:8798/" target="_blank" rel="noopener">管理资料</a>') +
    '<input id="library-folder-input" type="file" webkitdirectory multiple hidden><input id="library-file-input" type="file" multiple hidden><p class="tip">一次选择整个文件夹，保留子目录；笔记、图片和文档入库后可供 Easel 与纸飞机共同取材。单个文件最多 32 MiB。</p><div class="toolbar"><input id="library-query" class="search" type="search" placeholder="搜索资料或笔记内容" aria-label="搜索共享资料"><select id="library-source" aria-label="资料来源"><option value="">全部来源</option></select></div><p id="library-status" role="status">正在读取…</p><div id="library-items" class="cards"></div>';
  let searchSequence = 0, timer;
  $('#open-feishu').onclick=()=>void drawFeishuOnline();
  async function load() {
    const search = ++searchSequence;
    try {
      const value = await api('/library?q='+encodeURIComponent($('#library-query').value)+'&source='+encodeURIComponent($('#library-source').value));
      if (sequence !== sharedLibrarySequence || search !== searchSequence || location.hash !== route) return;
      const source = $('#library-source').value;
      $('#library-source').innerHTML = '<option value="">全部来源</option>'+[...new Set([...value.sources.map(s=>s.name),'新资料'])].map(s=>`<option>${esc(s)}</option>`).join('');
      $('#library-source').value = source;
      $('#library-status').textContent = `${value.total} 份资料 · 当前显示 ${value.items.length} 份`;
      $('#library-items').innerHTML = value.items.map(i=>`<article class="idea-card"><h3>${esc(i.name)}</h3><p>${esc(i.source)} · v${i.version}</p><p class="muted">${esc(i.relative_path)}</p><div class="actions"><a class="button" href="http://127.0.0.1:8798/api/items/${i.id}/content?version=${i.version}" target="_blank" rel="noopener">下载</a>${/\.(md|txt|csv|png|jpe?g|gif|webp)$/i.test(i.name)?`<button class="button primary" data-material-id="${i.id}" data-material-version="${i.version}">${/\.(md|txt|csv)$/i.test(i.name)?'存入选题素材':'导入图片素材'}</button>`:''}</div></article>`).join('') || '<p>没有找到资料。</p>';
    } catch(e) { if (sequence === sharedLibrarySequence && location.hash === route) $('#library-status').textContent=e.message; }
  }
  $('#library-query').oninput=()=>{clearTimeout(timer);timer=setTimeout(load,250);};
  $('#library-source').onchange=load;
  $('#library-items').onclick=async e=>{
    const button=e.target.closest('[data-material-id]'); if(!button || button.disabled)return;
    button.disabled=true;
    try {
      const result=await api('/library/import',{id:button.dataset.materialId,version:Number(button.dataset.materialVersion)});
      await refresh(false);
      if(result.kind==='note')location.hash='#edit/'+result.post.id;
      else toast('已导入图片素材');
    }catch(e){toast(e.message);}finally{button.disabled=false;}
  };
  $('#library-folder-button').onclick=()=>$('#library-folder-input').click();
  $('#library-file-button').onclick=()=>$('#library-file-input').click();
  async function upload(input, folder) {
    if (sharedLibraryUploading || !input.files.length) return;
    const files = [...input.files], errors = [];
    let done = 0, skipped = 0, failed = 0, source = '新资料';
    sharedLibraryUploading = true;
    const controls = ['#library-folder-button','#library-file-button','#open-feishu','#library-query','#library-source'];
    controls.forEach(s=>$(s).disabled=true);
    try {
      if (folder) source = (await api('/library/folder-import',{name:files[0].webkitRelativePath.split('/')[0]})).source;
      for (let n=0; n<files.length; n++) {
        const file = files[n], path = folder ? file.webkitRelativePath : file.name;
        if (file.size > 32*1048576 || !/\.(md|txt|html?|pdf|png|jpe?g|webp|gif|svg|csv|docx|pptx|xlsx)$/i.test(file.name) || path.split('/').some(p=>p.startsWith('.') || p.startsWith('_') || ['node_modules','logs','backups'].includes(p))) {skipped++;continue;}
        $('#library-status').textContent = `正在导入 ${n+1}/${files.length}：${path}`;
        try {
          const content = await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});
          await api('/library/upload',{name:file.name,source,content,...(folder?{relative_path:path}:{})});
          done++;
        } catch (e) {failed++;if(errors.length<5)errors.push(path+'：'+e.message);}
      }
      await load();
      $('#library-status').textContent = `导入完成：成功 ${done} 份，跳过 ${skipped} 份，失败 ${failed} 份。`+(folder?' 子目录已保留，Easel 可从同一库取材。':'')+(errors.length?' '+errors.join('；'):'');
    } catch (e) {$('#library-status').textContent=e.message;}
    finally {input.value='';sharedLibraryUploading=false;controls.forEach(s=>{if($(s))$(s).disabled=false;});}
  }
  $('#library-folder-input').onchange=e=>void upload(e.target,true);
  $('#library-file-input').onchange=e=>void upload(e.target,false);
  await load();
}

async function drawFeishuOnline(){
  ++sharedLibrarySequence;
  $('#main').innerHTML=heading('飞书在线资料','直接访问飞书原文；保存在线引用后，生成任务按需读取最新正文。','<button class="button" id="back-library">本地资料</button><a class="button" href="http://127.0.0.1:8798/feishu" target="_blank" rel="noopener">授权范围与 API 工具</a>')+
    '<p id="feishu-auth" role="status">正在核验授权…</p><form id="feishu-search" class="toolbar"><input id="feishu-query" maxlength="30" placeholder="搜索飞书资料" aria-label="搜索飞书资料"><button class="button">搜索</button></form><form id="feishu-link" class="toolbar"><input id="feishu-url" type="url" placeholder="飞书文档或知识库链接" aria-label="飞书文档链接"><button class="button">读取原文</button></form><p id="feishu-status" role="status"></p><div id="feishu-results" class="cards"></div><button id="feishu-next" class="button" hidden>下一页</button><pre id="feishu-content" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>';
  $('#back-library').onclick=()=>void drawSharedLibrary();
  let next='',query='';
  async function read(url){try{$('#feishu-status').textContent='正在从飞书读取…';const d=await api('/feishu/document?url='+encodeURIComponent(url));$('#feishu-content').textContent=d.content;$('#feishu-status').textContent=`在线原文 · 飞书版本 ${d.revision_id??'未提供'}`;}catch(e){if($('#feishu-status'))$('#feishu-status').textContent=e.message;}}
  async function search(page=''){try{$('#feishu-status').textContent='正在搜索…';const v=await api('/feishu/search?q='+encodeURIComponent(query)+'&page_token='+encodeURIComponent(page));$('#feishu-results').innerHTML='';for(const r of v.results||[]){let url;try{url=new URL(r.url);}catch{continue;}if(url.protocol!=='https:'||!(url.hostname.endsWith('.feishu.cn')||url.hostname.endsWith('.larksuite.com')))continue;const title=String(r.title||r.title_highlighted||'飞书资料').replace(/<[^>]*>/g,'');const card=document.createElement('article');card.className='idea-card';card.innerHTML=`<h3>${esc(title)}</h3><a class="button" href="${esc(url.href)}" target="_blank" rel="noopener">在飞书打开</a>`;if(/\/(wiki|docx)\//.test(url.pathname)){for(const [label,fn] of [['读取原文',()=>read(url.href)],['保存在线引用',async()=>{await api('/feishu/reference',{url:url.href,title});await refresh(false);toast('已保存在线引用，未复制笔记正文');}]]){const b=document.createElement('button');b.className='button';b.textContent=label;b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){toast(e.message);}finally{b.disabled=false;}};card.append(b);}}$('#feishu-results').append(card);}next=v.has_more?v.page_token||'':'';$('#feishu-next').hidden=!next;$('#feishu-status').textContent=`本页 ${v.results?.length||0} 份在线结果`;}catch(e){if($('#feishu-status'))$('#feishu-status').textContent=e.message;}}
  $('#feishu-search').onsubmit=e=>{e.preventDefault();query=$('#feishu-query').value;void search();};$('#feishu-link').onsubmit=e=>{e.preventDefault();void read($('#feishu-url').value);};$('#feishu-next').onclick=()=>void search(next);
  try{const s=await api('/feishu/status');if($('#feishu-auth'))$('#feishu-auth').textContent=`${s.user_name||'飞书用户'} · ${s.connected?'已连接':'需要重新授权'} · 实际授权 ${s.scope_count} 项`;}catch(e){if($('#feishu-auth'))$('#feishu-auth').textContent=e.message;}
}
