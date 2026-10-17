"use strict";
let articleVideos=[];
async function loadArticleVideos(postId) {
  const data=await api('/posts/'+postId+'/videos');
  if(editor?.id!==postId)return;
  articleVideos=data.videos;
  const node=$('#article-videos');if(!node)return;
  node.innerHTML=`<div class="panel-head"><h2>本篇视频素材</h2><button class="button small" data-video-action="open">添加 / 管理视频</button></div><div class="panel-body">${articleVideos.length?articleVideos.map(v=>`<div class="article-video-summary"><strong>${esc(v.title)}</strong><p>${esc(v.description)}</p><button class="button small" data-video-action="insert" data-id="${v.id}" data-position="opening">放在文章开场</button></div>`).join(''):'<p class="tip">添加仿真或演示视频，并写明内容。AI会读取本篇的视频说明，写作时便能围绕真实素材安排开场。</p>'}</div>`;
  const assistant=document.querySelector('.editor-assistant[data-assistant-kind="post"]');if(assistant)void assistantRefresh(assistant);
}
async function openArticleVideos() {
  if(!editor)return;
  const postId=editor.id;await loadArticleVideos(postId);if(editor?.id!==postId)return;
  modal('本篇视频素材',`<p class="tip">MP4小于10 MiB。视频保留在本机；AI根据你填写的说明理解素材。公众号图文请先在微信编辑页“本地上传”成片，插入保存后在这里关联；API视频素材用于菜单、自动回复等，不能代替图文视频。</p><div class="video-import-form"><label>选择视频<input id="video-file" type="file" accept="video/mp4,.mp4"></label><label>标题<input id="video-title" maxlength="120" placeholder="例如：MicroDuck篮球平衡仿真"></label><label>视频内容与写作说明<textarea id="video-description" rows="3" maxlength="4000" placeholder="画面展示了什么？仿真还是实机？希望放在哪里？"></textarea></label><button class="button primary" data-video-action="import" data-post="${postId}">加入本篇素材</button></div><div class="video-manager-list">${articleVideos.map(v=>`<section class="panel video-manager-item" data-video-id="${v.id}" data-version="${v.version}"><video controls preload="metadata" playsinline src="${v.url}"></video><label>标题<input data-video-title value="${esc(v.title)}" maxlength="120"></label><label>内容说明<textarea data-video-description rows="3" maxlength="4000">${esc(v.description)}</textarea></label><label>微信或腾讯视频链接（可选）<input data-video-link value="${esc(v.embed_url)}" placeholder="填写微信原生视频或腾讯视频播放链接；API素材地址不等于正文播放器"></label><p class="tip">${v.wechat_uploaded?'API素材已上传（菜单/自动回复）':'本机视频素材'}${v.player_ready?' · 已关联播放器地址，实际播放待微信预览核对':v.embed_url?' · 图文嵌入尚不可用，请在微信编辑页插入视频':' · 尚未关联可用播放器'}${v.error?' · '+esc(v.error):''}</p><div class="actions"><button class="button small" data-video-action="save">保存说明与链接</button><button class="button small" data-video-action="native">从微信草稿关联视频</button><button class="button small" data-video-action="insert" data-position="opening" data-id="${v.id}">插到开场</button><button class="button small" data-video-action="insert" data-position="cursor" data-id="${v.id}">插到光标处</button></div></section>`).join('')}</div>`,'<button class="button" data-action="close">关闭</button>');
  $('#video-file').onchange=()=>{const f=$('#video-file').files[0];if(f&&!$('#video-title').value)$('#video-title').value=f.name.replace(/\.mp4$/i,'').slice(0,120);};
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-video-action]');if(!button||button.disabled)return;
  const action=button.dataset.videoAction;
  try{
    if(action==='open'){await openArticleVideos();return;}
    if(action==='insert'){
      const item=articleVideos.find(v=>v.id===button.dataset.id);if(!item||item.post_id!==editor?.id)throw new Error('文章已切换，请重新打开视频素材');
      const area=$('#edit-body'),marker=`@[${item.title.replace(/[\]\r\n]/g,'')}](${item.url})`;
      if(area.value.includes(item.url))throw new Error('正文已经引用这个视频，请直接移动视频标记');
      if(button.dataset.position==='opening')area.value=marker+'\n\n'+area.value;
      else area.setRangeText('\n\n'+marker+'\n\n',area.selectionStart,area.selectionEnd,'end');
      markDirty();void preview();if($('#modal').open)closeModal();toast('视频已放入编辑区，请保存稿件');return;
    }
    if(action==='import'){
      const postId=button.dataset.post,file=$('#video-file').files[0];
      if(!file||!file.name.toLowerCase().endsWith('.mp4')||file.size>=10*1024*1024)throw new Error('请选择小于10 MiB的MP4文件');
      const values={filename:file.name,title:$('#video-title').value,description:$('#video-description').value};
      button.disabled=true;values.data=await fileToBase64(file);await api('/posts/'+postId+'/videos',values);
      if(editor?.id===postId)await openArticleVideos();return;
    }
    const section=button.closest('[data-video-id]'),id=section.dataset.videoId;
    button.disabled=true;
    if(action==='save')await api('/videos/'+id,{version:Number(section.dataset.version),title:section.querySelector('[data-video-title]').value,description:section.querySelector('[data-video-description]').value,embed_url:section.querySelector('[data-video-link]').value});
    if(action==='wechat')await api('/videos/'+id+'/wechat',{});
    if(action==='native')await api('/videos/'+id+'/wechat-draft',{version:Number(section.dataset.version)});
    await openArticleVideos();
  }catch(error){if(button.isConnected)button.disabled=false;if($('#modal').open)modalError(error);else toast(error.message);}
});
