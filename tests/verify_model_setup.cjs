const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const out=path.resolve(__dirname,'../../work/model-setup-20261009'),fixture=JSON.parse(fs.readFileSync(path.join(out,'fixture.json'),'utf8'));
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});try{
const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(fixture.url);await page.locator('#model-setup-dialog[open]').waitFor();
await page.locator('[data-setup="later"]').click();await page.waitForFunction(()=>!document.querySelector('#model-setup-dialog').open);
await page.reload();await page.waitForFunction(()=>modelSetupState!==null);assert.equal(await page.locator('#model-setup-dialog[open]').count(),0);
await page.locator('#model-setup-notice button').click();await page.locator('#model-wizard[open]').waitFor();
await page.locator('#wizard-base').fill(fixture.model);await page.locator('#wizard-key').fill('synthetic-ui-key');await page.locator('#wizard-model').fill('fixture-model');
await page.locator('#model-wizard [data-wizard="next"]').click();await page.waitForFunction(()=>modelWizard?.step===1&&!modelWizard.busy);
assert((await page.evaluate(()=>api('/model-setup'))).configured);assert(await page.locator('#model-setup-notice').isHidden());
await page.locator('#model-wizard [data-wizard="skip"]').click();await page.waitForFunction(()=>modelWizard?.step===2&&!modelWizard.busy);
await page.locator('#model-wizard [data-wizard="finish"]').click();await page.reload();await page.waitForFunction(()=>modelSetupState!==null);
assert.equal(await page.locator('#model-setup-dialog[open]').count(),0);assert.deepEqual(errors,[]);
console.log('First-use skip and wizard integration passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
