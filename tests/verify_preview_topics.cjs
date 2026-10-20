const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('node:assert/strict');

(async () => {
  const { url, screenshot } = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(url + '/editor/');
    await page.getByRole('button', { name: '帮我选题' }).click();
    const dialog = page.getByRole('dialog', { name: '选题策划' });
    await dialog.getByRole('option', { name: '合成账号' }).waitFor({ state: 'attached' });
    await dialog.getByLabel('使用画像').selectOption({ label: '合成账号' });
    await dialog.getByLabel('本次想法或素材摘要').fill('写一篇让读者验证 Agent 记忆的文章');
    await dialog.getByRole('button', { name: '返回文章' }).click();
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await dialog.getByLabel('本次想法或素材摘要').inputValue(), '写一篇让读者验证 Agent 记忆的文章');
    await dialog.getByRole('button', { name: '生成 3 个候选选题' }).click();
    await dialog.getByRole('heading', { name: '合成候选1' }).waitFor();
    await page.screenshot({ path: screenshot });
    assert.equal(await dialog.getByRole('article').count(), 3);
    await dialog.getByRole('article').nth(1).getByRole('button', { name: '选这个，创建选题文章' }).click();
    await page.waitForFunction(() => document.querySelector('input[aria-label="文章标题"]')?.value === '合成候选2');
    assert.equal(await page.getByLabel('文章标题').inputValue(), '合成候选2');
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
