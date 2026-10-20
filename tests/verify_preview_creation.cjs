const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));fs.mkdirSync(f.output,{recursive:true});
 const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:940}}),errors=[];
 page.on('pageerror',err=>errors.push(String(err)));page.on('dialog',d=>d.accept());
 const panel=page.getByRole('dialog',{name:'本篇创作',exact:true});
 const body=page.getByLabel('文章正文',{exact:true});
 async function open(){await page.getByRole('button',{name:'素材与创作',exact:true}).click();await panel.waitFor();}
 async function api(endpoint,values){const token=(await (await page.request.get(f.url+'/api/v2/session')).json()).token;
  const response=await page.request.post(f.url+'/api/v2'+endpoint,{data:values,headers:{'X-Studio-Token':token}});assert.equal(response.status(),200,await response.text());return response.json();}
 try{
  await page.goto(f.url+'/editor/');await page.getByRole('button').filter({hasText:'M4创作浏览器验收'}).click();
  await body.waitFor();assert.equal(await body.inputValue(),'已经保存的原稿😀');
  await open();await panel.getByText('新增文字素材 / 导入文件',{exact:true}).click();
  await panel.getByLabel('素材标题',{exact:true}).fill('合成文字素材');await panel.getByLabel('素材来源',{exact:true}).fill('https://example.com/material');
  await panel.getByLabel('素材内容',{exact:true}).fill('仅此资料作为本轮依据，不得编造亲历。');
  await panel.getByRole('button',{name:'保存素材并选用',exact:true}).click();
  await panel.getByText('已选 1/6。只使用当前已保存文章和明确勾选的素材，任务保留各自版本快照。',{exact:true}).waitFor();
  assert(await panel.getByRole('checkbox').first().isChecked());
  await panel.getByLabel('创作要求',{exact:true}).fill('围绕本轮已选材料，先生成提纲。');
  await panel.getByRole('button',{name:'生成提纲',exact:true}).click();
  const outline=panel.getByLabel('编辑提纲',{exact:true});await outline.waitFor();
  await outline.fill('## 浏览器手工提纲😀\n\n保留事实、列出待补证据。');
  assert(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()));
  await panel.getByRole('button',{name:'返回文章',exact:true}).click();assert.equal(await body.inputValue(),'已经保存的原稿😀');
  await open();assert.equal(await outline.inputValue(),'## 浏览器手工提纲😀\n\n保留事实、列出待补证据。');
  await panel.getByRole('button',{name:'保存提纲',exact:true}).click();await panel.getByText('提纲已保存在本任务。确认前不会生成正文。',{exact:true}).waitFor();
  const old=(await (await page.request.get(f.url+'/api/v2/articles/'+f.post_id)).json());assert.equal(old.version,1);
  await page.screenshot({path:path.join(f.output,'outline-review.png'),fullPage:true});
  await panel.getByRole('button',{name:'确认提纲并生成正文',exact:true}).click();
  const pending=panel.getByLabel('修改待审稿',{exact:true});await pending.waitFor();
  assert((await pending.inputValue()).includes('合成完整正文'));
  if(f.mode==='direct'){
   await panel.getByRole('button',{name:'确认采用并保存文章',exact:true}).click();
   await panel.getByText('本次正文已采用为文章 v2。之后的文章修改不影响此历史记录。',{exact:true}).waitFor();
   await panel.getByRole('button',{name:'返回文章',exact:true}).click();
   assert.equal(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),false,'direct adoption must allow closing');
   await page.getByRole('button',{name:'保存文章',exact:true}).click();
   await page.getByText('已保存为第 3 版',{exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),false,'saved article must allow closing');
   await open();await panel.getByRole('navigation',{name:'创作历史',exact:true}).getByRole('button').filter({hasText:'已采用'}).click();
   assert.equal(await panel.getByRole('button',{name:'保存待审稿修改',exact:true}).count(),0,'applied history has no editable candidate');
   assert.equal(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),false,'viewing applied history must remain clean');
   assert.deepEqual(await page.evaluate(()=>window.PaperPlaneDesktop.getUnsavedAreas()),[]);
   await panel.getByRole('button',{name:'＋ 准备一次创作',exact:true}).click();
   await panel.getByText('新增文字素材 / 导入文件',{exact:true}).click();
   await panel.getByLabel('素材标题',{exact:true}).fill('尚未提交的素材');
   await panel.getByRole('button',{name:'返回文章',exact:true}).click();
   await page.getByText('创作有未保存输入',{exact:true}).waitFor();
   assert.deepEqual(await page.evaluate(()=>window.PaperPlaneDesktop.getUnsavedAreas()),['素材与创作：尚未保存的新素材']);
   await page.getByRole('button',{name:'保存文章',exact:true}).click();
   await page.getByText('已保存为第 4 版',{exact:true}).waitFor();
   assert(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),'article save must not discard unsaved material');
   await open();await panel.getByLabel('素材标题',{exact:true}).fill('');
   await panel.getByRole('button',{name:'返回文章',exact:true}).click();
   assert.equal(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),false);
   assert.deepEqual(errors,[]);return;
  }
  await pending.fill('作者手工审阅后的候选正文😀');await panel.getByRole('button',{name:'保存待审稿修改',exact:true}).click();
  await panel.getByRole('button',{name:'保存待审稿修改',exact:true}).waitFor();
  await page.waitForFunction(()=>[...document.querySelectorAll('.creation-content button')].find(b=>b.textContent==='保存待审稿修改')?.disabled);
  // Another page saves while the candidate is waiting; adoption must require current comparison.
  await api('/articles/'+f.post_id,{title:old.title,version:1,body:'另一窗口保存的当前原稿😀'});
  await panel.getByText('生成期间原稿已从 v1 变为 v2。先对照两份内容，再明确选择；原修订继续保留。',{exact:true}).waitFor();
  await panel.getByRole('button',{name:'保留当前稿，暂不采用',exact:true}).click();await panel.waitFor({state:'hidden'});
  assert.equal(await body.inputValue(),'已经保存的原稿😀'); // polling does not reset the main editor
  await open();await panel.getByRole('radio',{name:'手动合并后采用',exact:true}).check();
  await panel.getByLabel('合并后标题',{exact:true}).fill('M4合并采用文章');
  await panel.getByLabel('合并后正文',{exact:true}).fill('手动合并保留当前稿与候选😀');
  await page.screenshot({path:path.join(f.output,'conflict-merge.png'),fullPage:true});
  let release,arrived;const gate=new Promise(r=>release=r),entered=new Promise(r=>arrived=r);
  await page.route('**/creation/*/adopt',async route=>{const response=await route.fetch();arrived();await gate;await route.fulfill({response});});
  await panel.getByRole('button',{name:'确认采用并保存文章',exact:true}).click();await entered;
  // Expose the underlying editor to inject an edit after commit and before the response.
  await panel.evaluate(el=>el.close());await body.fill('采用回包到达前继续输入，必须保留😀');release();
  await page.unrouteAll({behavior:'wait'});await page.getByText('本次提交已保存；随后输入的内容仍未保存。',{exact:true}).waitFor();
  assert.equal(await body.inputValue(),'采用回包到达前继续输入，必须保留😀');
  const persisted=await (await page.request.get(f.url+'/api/v2/articles/'+f.post_id)).json();
  assert.equal(persisted.body,'手动合并保留当前稿与候选😀');assert.equal(persisted.version,3);assert.equal(persisted.author,'手写作者');
  await page.reload();await page.getByRole('button').filter({hasText:'M4合并采用文章'}).click();await open();
  await panel.getByRole('navigation',{name:'创作历史',exact:true}).getByRole('button').filter({hasText:'已采用'}).click();
  await panel.getByText('本次正文已采用为文章 v3。之后的文章修改不影响此历史记录。',{exact:true}).waitFor();
  assert((await panel.getByRole('region',{name:'本篇创作详情',exact:true}).innerText()).includes('手动合并保留当前稿与候选😀'));
  await page.setViewportSize({width:390,height:844});assert(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  const close=await panel.getByRole('button',{name:'返回文章',exact:true}).boundingBox();assert(close.y+close.height<=844);
  await page.screenshot({path:path.join(f.output,'mobile-history.png'),fullPage:true});assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(f.output,'result.json'),JSON.stringify({passed:true,checks:['material-save-select','outline-gate','edit-close-reopen','outline-save','review-before-adoption','candidate-edit','changed-current-compare','keep-current','manual-merge','late-adopt-reply-preserves-editor','saved-reload','mobile-bounds','no-console-errors']},null,2));
 }catch(error){await page.screenshot({path:path.join(f.output,'failure-'+Date.now()+'.png'),fullPage:true});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
