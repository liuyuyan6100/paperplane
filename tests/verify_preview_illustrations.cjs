const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
(async()=>{
 const f=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));fs.mkdirSync(f.output,{recursive:true});
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:940}}),errors=[];
 page.on('pageerror',err=>errors.push(String(err)));page.on('dialog',d=>d.accept());
 const panel=page.getByRole('dialog',{name:'本篇封面与配图',exact:true}),body=page.getByLabel('文章正文',{exact:true});
 const button=name=>panel.getByRole('button',{name,exact:true});
 async function open(){await page.getByRole('button',{name:'封面与配图',exact:true}).click();await panel.waitFor();}
 async function api(endpoint,values){const token=(await(await page.request.get(f.url+'/api/v2/session')).json()).token;const res=await page.request.post(f.url+'/api/v2'+endpoint,{data:values,headers:{'X-Studio-Token':token}});assert.equal(res.status(),200,await res.text());return res.json();}
 try {
  await page.goto(f.url+'/editor/');await page.getByRole('button').filter({hasText:'M4配图浏览器验收'}).click();await body.waitFor();
  const before=await body.inputValue();await open();await button('建立配图计划').click();
  await panel.getByLabel('图标题',{exact:true}).waitFor();await panel.getByText('画像风格与参考图 · 合成测试画像',{exact:true}).click();
  assert.equal(await panel.getByAltText('画像真实参考图').count(),1);
  await page.screenshot({path:path.join(f.output,'plan-overview.png'),fullPage:true});
  await panel.getByLabel('画面描述',{exact:true}).fill('保留工程线稿和中文标题安全区，合成测试不充当截图。');
  await page.waitForFunction(()=>window.PaperPlaneDesktop.hasUnsaved());
  await button('返回文章').click();assert.equal(await body.inputValue(),before);await open();
  assert((await panel.getByLabel('画面描述',{exact:true}).inputValue()).includes('中文标题安全区'));
  await button('保存配图计划').click();await panel.getByText('配图计划已保存，文章保持原样。',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),false);
  await button('生成本页候选图').click();await panel.getByRole('img',{name:/候选图片/}).first().waitFor();
  const img=panel.getByRole('img',{name:/候选图片/}).first();await img.evaluate(el=>el.decode());assert(await img.evaluate(el=>el.complete&&el.naturalWidth>0));
  assert.equal((await(await page.request.get(f.url+'/api/v2/articles/'+f.post_id)).json()).version,1);
  await panel.locator('.image-candidates').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(f.output,'cover-candidate.png'),fullPage:true});
  await button('选择并预览').click();await button('预览采用位置').click();await panel.getByAltText('采用后的文章封面预览').waitFor();
  await button('确认采用图片并保存文章').click();await panel.getByText('图片已采用并保存为文章新版本，原修订与旧图继续保留。',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>window.PaperPlaneDesktop.hasUnsaved()),false);
  await panel.getByRole('button',{name:'2. 正文插图',exact:true}).click();
  await panel.getByLabel('上传本页原图',{exact:true}).setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:Buffer.from(f.png,'base64')});
  await panel.getByText('原图已作为本页候选保存，尚未写入文章。',{exact:true}).waitFor();
  await button('选择并预览').click();await panel.getByLabel('采用图片说明',{exact:true}).fill('合成测试插图，非真实截图');
  await button('预览采用位置').click();await panel.getByText(/预览当前文章 v2/).waitFor();
  await panel.locator('.image-adoption-preview').scrollIntoViewIfNeeded();await panel.locator('.image-adoption-preview').evaluate(el=>el.scrollTop=el.scrollHeight);await page.screenshot({path:path.join(f.output,'body-placement-preview.png'),fullPage:true});
  let release,arrived;const gate=new Promise(r=>release=r),entered=new Promise(r=>arrived=r);
  await page.route('**/illustrations/*/adopt',async route=>{const response=await route.fetch();arrived();await gate;await route.fulfill({response});});
  await button('确认采用图片并保存文章').click();await entered;
  await panel.evaluate(el=>el.close());await body.fill('采用回包前新输入，必须保留😀');release();
  await page.unrouteAll({behavior:'wait'});await page.getByText('本次提交已保存；随后输入的内容仍未保存。',{exact:true}).waitFor();
  assert.equal(await body.inputValue(),'采用回包前新输入，必须保留😀');
  const saved=await(await page.request.get(f.url+'/api/v2/articles/'+f.post_id)).json();
  assert.equal(saved.version,3);assert(saved.body.includes('图注：合成测试插图，非真实截图'));assert(saved.body.indexOf('/media/')<saved.body.indexOf('— 合成作者'));
  await body.fill(saved.body);await page.waitForFunction(()=>!window.PaperPlaneDesktop.hasUnsaved());await page.locator('dialog[aria-label="本篇封面与配图"]').evaluate(el=>el.showModal());
  // Polling a newer plan must preserve locally typed planning input.
  await panel.getByLabel('画面描述',{exact:true}).fill('本地尚未保存的画面要求😀');
  const plans=await(await page.request.get(f.url+'/api/v2/articles/'+f.post_id+'/illustrations')).json(),pid=plans.plans[0].id;
  const plan=await(await page.request.get(f.url+'/api/v2/articles/'+f.post_id+'/illustrations/'+pid)).json();
  const pages=plan.pages.map(p=>Object.fromEntries(['title','text','prompt','slot','aspect','placement','caption'].filter(k=>p[k]!==undefined).map(k=>[k,p[k]])));
  await api('/articles/'+f.post_id+'/illustrations/'+pid+'/edit',{version:plan.version,pages});
  await button('放弃本地输入并读取新版计划').waitFor();assert.equal(await panel.getByLabel('画面描述',{exact:true}).inputValue(),'本地尚未保存的画面要求😀');
  await button('放弃本地输入并读取新版计划').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(f.output,'plan-conflict-preserves-input.png'),fullPage:true});
  await button('放弃本地输入并读取新版计划').click();await page.waitForFunction(()=>!window.PaperPlaneDesktop.hasUnsaved());
  await page.setViewportSize({width:390,height:844});assert(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  const close=await button('返回文章').boundingBox();assert(close.y+close.height<=844);
  await page.screenshot({path:path.join(f.output,'mobile-plan.png'),fullPage:true});
  await button('返回文章').click();await page.reload();await page.getByRole('button',{name:'文章列表',exact:true}).click();await page.getByRole('button').filter({hasText:'M4配图浏览器验收'}).click();
  assert.equal(await body.inputValue(),saved.body);await open();await panel.getByRole('navigation',{name:'配图历史'}).getByRole('button').filter({hasText:'计划 v'}).click();await panel.getByLabel('图标题',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(f.output,'result.json'),JSON.stringify({passed:true,checks:['saved-snapshot','actual-profile-reference','plan-input-close-reopen','generate-review','cover-preview-adopt','body-original-upload','placement-caption','old-article-kept-until-adopt','late-adopt-reply-preserves-new-input','clean-close-state','polling-preserves-plan-edit','mobile-bounds','reload-persistence']},null,2));
 } catch(error){await page.screenshot({path:path.join(f.output,'failure-'+Date.now()+'.png'),fullPage:true});throw error;} finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
