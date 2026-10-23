const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('assert/strict');
(async () => {
 const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
 const browser = await chromium.launch({headless:true});
 const page = await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.goto(fixture.url+'/editor/');
  await page.getByText('合成草稿衔接测试',{exact:true}).click();
  await page.getByRole('button',{name:'检查与公众号草稿',exact:true}).click();
  const panel=page.getByRole('dialog',{name:'检查与公众号草稿',exact:true});
  await panel.getByText('有问题需处理',{exact:false}).waitFor();
  await panel.getByRole('button',{name:'刷新检查',exact:true}).click();
  await panel.getByRole('button',{name:'检查并送入公众号草稿箱',exact:true}).click();
  await panel.getByRole('heading',{name:'等待补齐 · 保存后自动继续'}).waitFor();
  await page.screenshot({path:fixture.screenshot,fullPage:true});
  await panel.getByRole('button',{name:'停止本次送稿等待'}).click();
  await panel.getByRole('heading',{name:'已停止等待',exact:true}).waitFor();
  await panel.getByRole('button',{name:'返回文章',exact:true}).click();
  const body=page.locator('textarea').first();
  // Use the labelled editor to retain an uncommitted edit while opening the new module.
  const editor=page.locator('textarea').filter({hasText:'[待核实] 浏览器合成内容'});
  const target=await editor.count()?editor.first():body;
  await target.fill('[待核实] 浏览器合成内容，未保存修改');
  await page.getByRole('button',{name:'检查与公众号草稿',exact:true}).click();
  await panel.getByText('编辑区有未保存修改。检查使用已保存版本，请先返回文章保存。').waitFor();
  assert(await panel.getByRole('button',{name:'检查并送入公众号草稿箱',exact:true}).isDisabled());
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:fixture.screenshot.replace('.png','-mobile.png'),fullPage:true});
  const bounds=await panel.boundingBox(); assert(bounds.width<=390 && bounds.x>=0);
  await panel.getByRole('button',{name:'返回文章',exact:true}).click();
  assert((await target.inputValue()).includes('未保存修改'));
  assert.deepEqual(errors,[]);
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
