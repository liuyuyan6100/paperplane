const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));fs.mkdirSync(f.output,{recursive:true});
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:940}}),errors=[];
 let consent=true;page.on('dialog',d=>consent?d.accept():d.dismiss());page.on('pageerror',e=>errors.push(String(e)));
 const scope=page.getByRole('navigation',{name:'文章范围',exact:true});
 const row=()=>page.getByRole('button',{name:'管理测试稿的操作',exact:true});
 async function menu(action){if(await row().getAttribute('aria-expanded')!=='true')await row().click();await page.getByRole('group',{name:'管理测试稿操作',exact:true}).getByRole('button',{name:action,exact:true}).click();}
 async function count(n){await page.waitForFunction(n=>document.querySelectorAll('.article-more').length===n,n);}
 try{
  await page.goto(f.url+'/editor/');await page.getByRole('button',{name:'设置',exact:true}).click();
  const settings=page.getByRole('dialog',{name:'设置',exact:true}),select=settings.getByLabel('关闭窗口行为',{exact:true});
  await select.selectOption('exit');await settings.getByText('关闭习惯已保存，下一次关闭窗口生效。',{exact:true}).waitFor();
  await page.screenshot({path:path.join(f.output,'close-settings.png'),fullPage:true});
  await settings.getByRole('button',{name:'关闭设置',exact:true}).click();await page.reload();
  await page.getByRole('button',{name:'设置',exact:true}).click();await select.waitFor();
  await page.waitForFunction(()=>document.querySelector('[aria-label="关闭窗口行为"]').value==='exit');assert.equal(await select.inputValue(),'exit');
  await select.selectOption('tray');await settings.getByText('关闭习惯已保存，下一次关闭窗口生效。',{exact:true}).waitFor();
  await settings.getByRole('button',{name:'关闭设置',exact:true}).click();
  await page.getByRole('button').filter({hasText:'管理测试稿'}).first().click();
  const body=page.getByLabel('文章正文',{exact:true});await body.fill('尚未保存的输入😀');
  consent=false;await menu('归档');assert.equal(await body.inputValue(),'尚未保存的输入😀');await count(1);
  consent=true;
  let entered,release;const arrived=new Promise(r=>entered=r),gate=new Promise(r=>release=r);
  await page.route('**/articles/*/manage',async route=>{const response=await route.fetch();entered();await gate;await route.fulfill({response});});
  await menu('归档');await arrived;assert(await page.locator('.editor-main').evaluate(el=>el.inert));release();await page.unrouteAll({behavior:'wait'});
  await count(0);await scope.getByRole('button',{name:'归档',exact:true}).click();await count(1);
  await page.getByRole('button').filter({hasText:'管理测试稿'}).first().click();
  const reading=page.getByRole('dialog',{name:'文章只读查看',exact:true});await reading.getByText('已保存的合成正文😀',{exact:true}).waitFor();
  await reading.getByRole('button',{name:'关闭查看',exact:true}).click();
  await menu('删除（移入回收站）');await count(0);await scope.getByRole('button',{name:'回收站',exact:true}).click();await count(1);
  await row().click();await page.screenshot({path:path.join(f.output,'trash-restore.png'),fullPage:true});
  await page.getByRole('button',{name:'恢复文章',exact:true}).click();await count(0);
  await scope.getByRole('button',{name:'归档',exact:true}).click();await count(1);await menu('取消归档');await count(0);
  await scope.getByRole('button',{name:'文章',exact:true}).click();await count(1);
  await page.getByRole('button').filter({hasText:'管理测试稿'}).first().click();assert.equal(await body.inputValue(),'已保存的合成正文😀');
  await page.locator('.article-row').click({button:'right'});await page.getByRole('group',{name:'管理测试稿操作',exact:true}).waitFor();
  await page.screenshot({path:path.join(f.output,'article-actions.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'文章列表',exact:true}).click();
  assert(await page.locator('.library').evaluate(el=>el.scrollWidth<=el.clientWidth+1));assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(f.output,'result.json'),JSON.stringify({passed:true,checks:['settings-persist-reload','dirty-cancel-preserves','inert-during-mutation','archive-read-only','trash-and-restore-to-archive','unarchive','right-click-menu','mobile-bounds','no-console-errors']},null,2));
 }catch(e){await page.screenshot({path:path.join(f.output,'failure.png'),fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
