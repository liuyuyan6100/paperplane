const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('node:assert/strict');

(async () => {
  const { url, screenshot } = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 850 } });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(url + '/editor/');
    await page.getByRole('button', { name: '管理画像' }).click();
    const dialog = page.getByRole('dialog', { name: '管理账号画像' });
    await dialog.getByLabel('画像名称').fill('合成 P1 画像');
    await dialog.getByLabel('定位与内容方向').fill('可验证的教程');
    await dialog.getByLabel('目标读者').fill('入门读者');
    await dialog.getByLabel('运营目标').fill('完成一篇清晰文章');
    await dialog.getByRole('button', { name: '保存画像' }).click();
    await dialog.getByText('画像已保存。', { exact: false }).waitFor();
    await page.screenshot({ path: screenshot });
    await dialog.getByRole('button', { name: '返回文章' }).click();
    await page.locator('.meta-line select').nth(1).selectOption({ label: '合成 P1 画像' });
    await page.getByLabel('文章标题').fill('合成 P1 文章');
    const [, saved] = await Promise.all([
      page.getByRole('button', { name: '保存文章' }).click(),
      page.waitForResponse(response => response.url().endsWith('/api/v2/articles') && response.request().method() === 'POST'),
    ]);
    assert.equal(saved.status(), 200);
    await page.getByRole('button', { name: '管理画像' }).click();
    await dialog.getByLabel('目标读者').fill('入门读者与编辑');
    assert.equal(await dialog.getByRole('button', { name: '保存画像' }).isEnabled(), true, 'dirty edit enables save');
    await dialog.getByRole('button', { name: '返回文章' }).click();
    assert.equal(await dialog.getByLabel('目标读者').inputValue(), '入门读者与编辑');
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await dialog.getByRole('button', { name: '保存画像' }).isEnabled(), true, 'cancel close retains dirty edit');
    const token = (await (await page.request.get(url + '/api/v2/session')).json()).token;
    const id = (await (await page.request.get(url + '/api/v2/profiles')).json()).profiles[0].id;
    const current = await (await page.request.get(url + '/api/v2/profiles/' + id)).json();
    const external = await page.request.post(url + '/api/v2/profiles/' + id, {
      headers: { 'X-Studio-Token': token, 'Content-Type': 'application/json' },
      data: { name: current.name, direction: current.direction, audience: '另一窗口读者', goal: current.goal, version: current.version },
    });
    assert.equal(external.status(), 200);
    assert.equal(await dialog.getByRole('button', { name: '保存画像' }).isEnabled(), true, 'external save retains local dirty edit');
    await dialog.getByRole('button', { name: '保存画像' }).click();
    await dialog.getByRole('alert').getByText(/其他页面修改/).waitFor();
    assert.equal(await dialog.getByLabel('目标读者').inputValue(), '入门读者与编辑');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
