const { chromium } = require('playwright');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
(async () => {
  const f = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  fs.mkdirSync(f.output, {recursive:true});
  const browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:960}});
  const page = await context.newPage(), errors=[];
  page.on('pageerror', e=>errors.push(String(e)));
  const panel = page.getByRole('complementary', {name:'侧边 AI'});
  const body = page.getByLabel('文章正文', {exact:true});
  const input = page.getByLabel('给 AI 的要求');
  async function send(message, count) {
    await input.fill(message); await input.press('Control+Enter');
    await page.waitForFunction(n=>document.querySelectorAll('.assistant-turn').length===n,count);
    await panel.getByText('请求处理中，可继续编辑文章', {exact:true}).waitFor({state:'hidden'});
  }
  try {
    await page.goto(f.url+'/editor/');
    await page.getByLabel('文章标题',{exact:true}).fill('M2浏览器合成验收');
    await body.fill('开始😀\n旧段落\n结束');
    await page.getByRole('button',{name:'保存文章',exact:true}).click();
    await page.getByText('已保存为第 1 版',{exact:true}).waitFor();
    await page.getByLabel('本次使用模型').selectOption('synthetic-alternate');
    // Plain Enter remains a newline; IME composition must never submit.
    await input.fill('键盘输入'); await input.press('Enter');
    assert.equal(await input.inputValue(),'键盘输入\n');
    await input.dispatchEvent('keydown',{key:'Enter',ctrlKey:true,isComposing:true});
    assert.equal(await panel.locator('.assistant-turn').count(),0);
    for (const viewport of [{width:1440,height:900},{width:1152,height:650},{width:900,height:650}]) {
      await page.setViewportSize(viewport);
      await page.evaluate(()=>{document.querySelector('.editing-document').scrollTop=5000;document.querySelector('.assistant-history').scrollTop=5000;});
      const bounds = await page.evaluate(()=>{
        const rect = selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};};
        return {save:rect('.toolbar .primary'),input:rect('.assistant-composer textarea'),send:rect('.assistant-composer .primary'),pageHeight:document.documentElement.scrollHeight,width:innerWidth,height:innerHeight};
      });
      for (const control of [bounds.save,bounds.input,bounds.send]) assert(control.top>=0&&control.bottom<=bounds.height+1&&control.right<=bounds.width+1,JSON.stringify({viewport,bounds}));
      assert(bounds.pageHeight<=bounds.height+1,JSON.stringify(bounds));
    }
    await page.setViewportSize({width:1440,height:900});
    await panel.getByRole('button',{name:'模型与生图设置',exact:true}).click();
    const settings=page.getByRole('dialog',{name:'模型与生图设置'});
    await settings.getByLabel('默认生图模型',{exact:true}).waitFor();
    await settings.getByText('合成风格画像', {exact:false}).click();
    await settings.getByText('合成测试水彩风格', {exact:true}).waitFor();
    const popupStarted=page.waitForEvent('popup');
    await settings.getByRole('link',{name:'管理账号画像与风格 ↗'}).click();
    const popup=await popupStarted; await popup.waitForURL('**/#profiles'); await popup.close();
    assert.equal(await body.inputValue(),'开始😀\n旧段落\n结束');
    await page.screenshot({path:path.join(f.output,'model-settings.png'),fullPage:true});
    await settings.getByLabel('讨论默认模型',{exact:true}).fill('synthetic-model');
    await settings.getByRole('button',{name:'保存模型设置',exact:true}).click();
    await settings.waitFor({state:'hidden'});
    assert.equal(await body.inputValue(),'开始😀\n旧段落\n结束');
    const sent=page.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith('/assistant'));
    await send('讨论表达是否清楚',1);
    assert.equal((await sent).postDataJSON().model,'synthetic-alternate');
    await panel.getByText('合成讨论回复',{exact:true}).waitFor();
    assert.equal(await panel.getByRole('button',{name:'填入编辑区',exact:true}).count(),0);
    await panel.getByRole('button',{name:'修改文章',exact:true}).click();
    await body.evaluate(el=>{let i=el.value.indexOf('旧段落'); el.focus(); el.setSelectionRange(i,i+3);});
    await panel.getByRole('button',{name:'使用选中片段',exact:true}).click();
    await send('只修改选中片段',2);
    await panel.getByText('合成修改回复',{exact:true}).waitFor();
    await panel.getByText('查看建议与差异',{exact:true}).click();
    await page.screenshot({path:path.join(f.output,'assistant-desktop.png'),fullPage:true});
    const fill = panel.getByRole('button',{name:'填入编辑区',exact:true});
    await fill.click();
    await page.getByText('建议已填入编辑区，尚未保存。',{exact:true}).waitFor();
    assert.equal(await body.inputValue(),'开始😀\n片段已改😀\n结束');
    await panel.getByRole('button',{name:'撤销填入',exact:true}).click();
    assert.equal(await body.inputValue(),'开始😀\n旧段落\n结束');
    // Delay only delivery of the real accept response, then type before it arrives.
    await page.route('**/assistant/*/accept', async route=>{ const response=await route.fetch(); await new Promise(r=>setTimeout(r,400)); await route.fulfill({response}); });
    const acceptStarted=page.waitForRequest(r=>r.url().endsWith('/accept'));
    await fill.click(); await acceptStarted;
    await body.fill('等待时手写的内容');
    await panel.getByRole('alert').filter({hasText:'等待期间文章已变化'}).waitFor();
    assert.equal(await body.inputValue(),'等待时手写的内容');
    await page.unroute('**/assistant/*/accept');
    await panel.getByRole('button',{name:'继续调整',exact:true}).click();
    await send('基于当前手写内容继续调整',3);
    await panel.getByRole('button',{name:'填入编辑区',exact:true}).last().click();
    await page.getByText('建议已填入编辑区，尚未保存。',{exact:true}).waitFor();
    assert.equal(await body.inputValue(),'等待时手写的内容\nAI建议');
    await page.getByRole('button',{name:'保存文章',exact:true}).click();
    await page.getByText('已保存为第 2 版',{exact:true}).waitFor();
    await page.reload();
    await page.getByRole('button').filter({hasText:'M2浏览器合成验收'}).click();
    assert.equal(await body.inputValue(),'等待时手写的内容\nAI建议');
    await page.waitForFunction(()=>document.querySelectorAll('.assistant-turn').length===3);
    await send('模拟失败',4);
    await panel.getByText('合成失败，原稿保留',{exact:true}).waitFor();
    await panel.getByRole('button',{name:'重试本次请求',exact:true}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'显示 AI',exact:true}).click();
    assert(await input.isVisible());
    const mobile=await input.boundingBox();assert(mobile.y>=0&&mobile.y+mobile.height<=844);
    await page.screenshot({path:path.join(f.output,'assistant-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(f.output,'result.json'),JSON.stringify({passed:true,checks:['discuss','unicode-selection','diff','fill','undo','late-response','refine-current-draft','save-reopen-history','failure','mobile','short-window-controls-visible','ctrl-enter-send','plain-enter-newline','ime-no-send','catalog-selection-sent','settings-preserve-editor']},null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
