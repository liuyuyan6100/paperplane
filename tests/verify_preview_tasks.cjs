const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));fs.mkdirSync(f.output,{recursive:true});
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:900}});const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(String(error)));
 const center=page.getByRole('dialog',{name:'任务中心',exact:true});const panel=page.getByRole('complementary',{name:'侧边 AI'});
 const body=page.getByLabel('文章正文',{exact:true}),input=page.getByLabel('给 AI 的要求');
 const list=center.getByRole('navigation',{name:'任务列表'}),detail=center.getByRole('region',{name:'任务详情'});
 async function open(){await page.getByRole('button',{name:'任务中心',exact:true}).click();await center.waitFor();}
 async function choose(kind){await center.getByLabel('任务类型',{exact:true}).selectOption(kind);}
 try{
  await page.goto(f.url+'/editor/');
  await page.getByLabel('文章标题',{exact:true}).fill('M3浏览器执行验证');await body.fill('已经保存的正文');
  await page.getByRole('button',{name:'保存文章',exact:true}).click();await page.getByText('已保存为第 1 版',{exact:true}).waitFor();
  await body.fill('继续输入的未保存正文😀');await input.fill('实时任务：讨论表达，不修改文章');await input.press('Control+Enter');
  await panel.getByText('请求处理中，可继续编辑文章',{exact:true}).waitFor();
  await input.fill('还没发送的下一条要求');
  await open();await choose('assistant');
  await list.getByRole('button').filter({hasText:'M3浏览器执行验证'}).click();
  await detail.getByText('正在执行',{exact:true}).first().waitFor();
  const assistantId=await detail.locator('.task-id').innerText();
  await page.screenshot({path:path.join(f.output,'task-running.png'),fullPage:true});
  fs.writeFileSync(f.release,'release');
  await detail.getByText('已回复',{exact:true}).first().waitFor();
  assert.equal(await detail.locator('.task-id').innerText(),assistantId);
  // Polling and switching task selection must not save or replace the editor.
  await detail.getByRole('button',{name:'打开所属文章',exact:true}).click();await center.waitFor({state:'hidden'});
  assert.equal(await body.inputValue(),'继续输入的未保存正文😀');assert.equal(await input.inputValue(),'还没发送的下一条要求');
  await open();await center.getByLabel('仅当前文章',{exact:true}).check();await choose('assistant');
  await list.getByRole('button').filter({hasText:'M3浏览器执行验证'}).click();
  await detail.locator('.task-id').filter({hasText:assistantId}).waitFor();
  await center.getByLabel('仅当前文章',{exact:true}).uncheck();await choose('workflow');
  await list.getByRole('button').filter({hasText:'M3合成文章'}).click();
  await detail.getByText('执行失败',{exact:true}).first().waitFor();
  assert.equal(await detail.locator('.task-id').innerText(),f.workflow_id);
  let retryRequests=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/commands')&&r.postDataJSON().action==='retry')retryRequests++;});
  await detail.getByRole('button',{name:'重试原任务',exact:true}).evaluate(el=>{el.click();el.click();});
  await detail.getByText('待确认提纲',{exact:true}).first().waitFor();
  assert.equal(retryRequests,1);assert.equal(await detail.locator('.task-id').innerText(),f.workflow_id);
  const workflowPopup=page.waitForEvent('popup');await detail.getByRole('link',{name:'到原工作台处理 ↗'}).click();
  const workflow=await workflowPopup;await workflow.locator('#workflow-dialog[data-id="'+f.workflow_id+'"]').waitFor();await workflow.close();
  await page.screenshot({path:path.join(f.output,'task-outline-confirmation.png'),fullPage:true});
  await choose('delivery');await list.getByRole('button').filter({hasText:'M3合成送稿'}).click();
  await detail.getByText('结果待核对',{exact:true}).first().waitFor();
  assert.equal(await detail.getByRole('button',{name:'重试原任务',exact:true}).count(),0);
  const missing=detail.getByRole('button',{name:'标记为未收到',exact:true});assert(await missing.isDisabled());
  const deliveryPopup=page.waitForEvent('popup');await detail.getByRole('link',{name:'到原工作台处理 ↗'}).click();
  const queue=await deliveryPopup;await queue.locator('#modal[open]').waitFor();await queue.getByText('M3合成送稿',{exact:true}).first().waitFor();await queue.close();
  await page.screenshot({path:path.join(f.output,'task-unknown-delivery.png'),fullPage:true});
  await detail.getByRole('checkbox',{name:'我已到原平台核实，这篇文章确实未收到'}).check();await missing.click();
  await detail.getByText('执行失败',{exact:true}).first().waitFor();await detail.getByRole('button',{name:'重试原任务',exact:true}).waitFor();
  assert.equal(retryRequests,1,'verification must never resend');
  await page.setViewportSize({width:390,height:844});
  const bounds=await center.boundingBox();assert(bounds.x>=0&&bounds.y>=0&&bounds.x+bounds.width<=390&&bounds.y+bounds.height<=844);
  assert(await center.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  const close=await center.getByRole('button',{name:'关闭任务中心',exact:true}).boundingBox();assert(close.y+close.height<=844);
  await page.screenshot({path:path.join(f.output,'task-mobile.png'),fullPage:true});
  await page.keyboard.press('Escape');await center.waitFor({state:'hidden'});
  await page.setViewportSize({width:1440,height:900});
  assert.equal(await body.inputValue(),'继续输入的未保存正文😀');assert.equal(await input.inputValue(),'还没发送的下一条要求');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(f.output,'result.json'),JSON.stringify({passed:true,checks:['running-to-reply-same-id','close-keeps-unsaved-editor','keeps-unsent-ai-input','current-article-filter','retry-original-workflow-once','outline-confirmation-preserved','legacy-deep-links','unknown-delivery-no-retry','explicit-verified-then-manual-retry','mobile-dialog-bounds','polling-keeps-task-selection']},null,2));
 }finally{fs.writeFileSync(f.release,'release');await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
