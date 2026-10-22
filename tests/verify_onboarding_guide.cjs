const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('node:assert/strict');

(async () => {
  const { url, screenshot, mobileScreenshot } = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(url + '/editor/');
    await page.getByRole('button', { name: '新手入门' }).click();
    const guide = page.getByRole('dialog', { name: '新手入门流程' });
    await guide.getByRole('heading', { name: '跟着流程，完成第一篇文章' }).waitFor();
    assert.equal(await guide.getByRole('navigation', { name: '创作流程' }).getByRole('button').count(), 4);
    await page.screenshot({ path: screenshot });
    await guide.getByRole('button', { name: /02 选题/ }).click();
    assert.equal(await guide.getByText('选定一个候选，打开空白文章').isVisible(), true);
    await guide.getByRole('button', { name: '前往这一步' }).click();
    await page.getByRole('dialog', { name: '选题策划' }).waitFor();
    await page.getByRole('dialog', { name: '选题策划' }).getByRole('button', { name: '返回文章' }).click();
    await page.getByRole('button', { name: '新手入门' }).click();
    await guide.getByRole('button', { name: /01 准备/ }).click();
    await guide.getByRole('button', { name: /建立账号画像/ }).click();
    await guide.getByRole('button', { name: '前往这一步' }).click();
    await page.getByRole('dialog', { name: '管理账号画像' }).waitFor();
    await page.getByRole('dialog', { name: '管理账号画像' }).getByRole('button', { name: '返回文章' }).click();
    await page.getByRole('button', { name: /新建文章/ }).click();
    assert.equal(await page.getByRole('dialog', { name: '管理账号画像' }).isVisible(), false);
    assert.equal(await page.getByRole('dialog', { name: '选题策划' }).isVisible(), false);
    await page.getByRole('button', { name: '新手入门' }).click();
    await guide.getByRole('button', { name: /03 创作/ }).click();
    await guide.getByText('文章顶部「素材与创作」按钮；已有任务会打开待确认提纲').waitFor();
    assert.equal(await guide.getByRole('button', { name: '前往这一步' }).isDisabled(), true);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: mobileScreenshot });
    assert.equal(await guide.evaluate(element => element.scrollWidth <= element.clientWidth), true);
    await guide.getByRole('button', { name: '返回编辑器' }).click();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByLabel('文章标题', { exact: true }).fill('引导创作入口合成文章');
    await page.getByLabel('文章正文', { exact: true }).fill('测试保存后跳转，不发起模型请求。');
    await page.getByRole('button', { name: '保存文章', exact: true }).click();
    await page.getByRole('button', { name: '新手入门' }).click();
    await page.getByRole('dialog', { name: '新手入门流程' }).waitFor();
    assert.equal(await guide.getByRole('button', { name: '前往这一步' }).isDisabled(), false);
    await guide.getByRole('button', { name: '前往这一步' }).click();
    await page.getByRole('dialog', { name: '本篇创作' }).waitFor();
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
