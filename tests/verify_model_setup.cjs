const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
if(!process.argv[2])throw new Error('Run python -m pytest tests/test_onboarding_browser.py to create an isolated fixture.');
const fixturePath=path.resolve(process.argv[2]),out=path.dirname(fixturePath),fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
(async()=>{const browser=await chromium.launch({headless:true});try{
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
