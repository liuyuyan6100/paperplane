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
    await input.fill(message); await panel.getByRole('button',{name:'发送',exact:true}).click();
    await page.waitForFunction(n=>document.querySelectorAll('.assistant-turn').length===n,count);
    await panel.getByText('请求处理中，可继续编辑文章', {exact:true}).waitFor({state:'hidden'});
  }
  try {
    await page.goto(f.url+'/editor/');
    await page.getByLabel('文章标题',{exact:true}).fill('M2浏览器合成验收');
    await body.fill('开始😀\n旧段落\n结束');
    await page.getByRole('button',{name:'保存文章',exact:true}).click();
    await page.getByText('已保存为第 1 版',{exact:true}).waitFor();
    await send('讨论表达是否清楚',1);
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
    await page.screenshot({path:path.join(f.output,'assistant-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(f.output,'result.json'),JSON.stringify({passed:true,checks:['discuss','unicode-selection','diff','fill','undo','late-response','refine-current-draft','save-reopen-history','failure','mobile']},null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
