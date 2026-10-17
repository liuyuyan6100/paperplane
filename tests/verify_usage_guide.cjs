const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
if(!process.argv[2])throw new Error('Run python -m pytest tests/test_onboarding_browser.py to create an isolated fixture.');
const fixturePath=path.resolve(process.argv[2]),out=path.dirname(fixturePath),fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
(async()=>{const browser=await chromium.launch({headless:true});
const checks=[];try{
 const page=await browser.newPage({viewport:{width:1280,height:960}}),errors=[],writes=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')writes.push(new URL(r.url()).pathname);});
 const click=a=>page.locator(`#main [data-guide="${a}"]`).click();
 const step=async n=>{await page.locator(`#main [data-guide="step"][data-step="${n}"]`).click();await page.waitForFunction(n=>usageGuide.step===n&&!usageGuideBusy,n);};
 const reopen=async()=>{await page.locator('.topbar [data-guide="open"]').click();await page.locator('.usage-detail').waitFor();};
 const go=async d=>{await page.locator(`[data-destination="${d}"]`).click();};
 await page.goto(fixture.url);await page.locator('.usage-entry').waitFor();await page.waitForFunction(()=>usageGuide!==null);
 await page.evaluate(async()=>{usageGuide=await api('/usage-guide',{action:'reset'});});const before=await page.evaluate(()=>api('/state'));
 await page.locator('.usage-entry [data-guide="open"]').click();await page.locator('.usage-step-list').waitFor();assert.equal(await page.locator('.usage-step-list button').count(),6);checks.push('homepage_and_persistent_entries');
 await page.screenshot({path:path.join(out,'guide-desktop.png')});
 await go('models');await page.locator('#model-wizard[open]').waitFor();await page.locator('#model-wizard [data-wizard="close"]').first().click();checks.push('model_wizard_handoff');
 await step(1);await go('wechat');await page.locator('#wechat-mode').waitFor();await page.waitForFunction(()=>usageFocus===null);assert.equal(await page.locator('#wechat-mode').evaluate(e=>e===document.activeElement),true);assert(await page.locator('#usage-guide-dock').isVisible());checks.push('wechat_jump_and_context_hint');
 await page.locator('#wechat-app-id').fill('unsaved-fixture');await page.locator('#usage-guide-dock [data-guide="open"]').click();assert.equal(new URL(page.url()).hash,'#settings');assert.equal(await page.locator('#wechat-app-id').inputValue(),'unsaved-fixture');await page.locator('#nav [data-guide="open"]').click();assert.equal(new URL(page.url()).hash,'#settings');checks.push('unsaved_settings_and_sidebar_guard');
 await page.locator('#wechat-app-id').fill('');await reopen();
 await go('platforms');await page.locator('[data-managed-channel="wechat"]').waitFor();await reopen();checks.push('channel_jump');
 await step(2);await go('content');await page.locator('#main [data-action="new"]').first().waitFor();await reopen();await go('workflows');await page.waitForURL('**/#workflows');await reopen();checks.push('writing_destinations');
 await step(3);await go('content');await page.locator('#main [data-action="new"]').first().waitFor();await reopen();await go('illustrations');await page.waitForURL('**/#illustrations');await reopen();checks.push('visual_destinations');
 await step(4);await go('queue');await page.waitForURL('**/#queue');await page.locator('#usage-guide-dock [data-guide="pause"]').click();await page.waitForFunction(()=>!usageGuide.active&&!usageGuideBusy);await page.reload();await page.waitForFunction(()=>usageGuide!==null);assert.equal(await page.evaluate(()=>usageGuide.step),4);assert(await page.locator('#usage-guide-dock').isHidden());checks.push('pause_reload_and_resume');
 await reopen();await page.locator('[data-destination="queue"]').click();await page.waitForURL('**/#queue');await page.reload();await page.waitForFunction(()=>usageGuide?.active);assert(await page.locator('#usage-guide-dock').isVisible());checks.push('active_hint_survives_reload');await reopen();
 // Failure never advances progress or pretends to save.
 const original=await page.evaluate(()=>JSON.stringify(usageGuide));await page.route('**/api/usage-guide',r=>r.request().method()==='POST'?r.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'模拟保存失败'})}):r.continue());await click('complete');await page.waitForFunction(()=>usageGuideError.includes('未保存'));assert.equal(await page.evaluate(()=>JSON.stringify(usageGuide)),original);await page.unroute('**/api/usage-guide');checks.push('save_failure_preserves_progress');
 await click('reset');await page.waitForFunction(()=>usageGuide.step===0&&!usageGuideBusy);
 for(let i=0;i<6;i++){await click('complete');await page.waitForFunction(n=>usageGuide.completed.length===n&&!usageGuideBusy,i+1);}
 assert(await page.locator('#main').innerText().then(t=>t.includes('实际送稿与发表状态')));assert.equal(await page.evaluate(()=>usageGuide.active),false);checks.push('all_steps_complete_without_publication');
 const after=await page.evaluate(()=>api('/state'));assert.deepEqual(after.posts,before.posts);assert.deepEqual(after.jobs,before.jobs);assert.deepEqual(after.channels,before.channels);
 assert(writes.every(p=>p==='/api/usage-guide'),JSON.stringify(writes));checks.push('no_model_or_content_or_channel_writes');
 // Guide entry protects unsaved editor and an existing modal.
 await page.evaluate(()=>{dirty=true;location.hash;});await click('previous');assert.equal(await page.evaluate(()=>usageGuide.step),5);await page.evaluate(()=>dirty=false);checks.push('dirty_editor_guard');
 await page.evaluate(()=>modal('临时操作','未关闭的操作',''));await page.evaluate(()=>document.querySelector('.topbar [data-guide="open"]').click());assert.equal(await page.locator('#modal[open]').count(),1);await page.evaluate(()=>closeModal());checks.push('existing_modal_preserved');
 await click('reset');await page.waitForFunction(()=>usageGuide.step===0&&!usageGuideBusy);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'guide-mobile.png')});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks.push('mobile_guide_fits');
 await step(1);await go('wechat');await page.locator('#wechat-mode').waitFor();await page.locator('#toast').evaluate(e=>e.classList.remove('show'));await page.screenshot({path:path.join(out,'guide-mobile-context.png')});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks.push('mobile_context_fits');
 assert.deepEqual(errors,[]);checks.push('no_browser_errors');
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({passed:true,checks},null,2));console.log(JSON.stringify({passed:true,checks},null,2));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
