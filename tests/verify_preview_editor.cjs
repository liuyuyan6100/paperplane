const { chromium } = require("playwright");
const fs = require("fs"),
  path = require("path"),
  assert = require("node:assert/strict");
(async () => {
  const f = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  fs.mkdirSync(f.output, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
  });
  const errors = [];
  context.on("page", (page) =>
    page.on("pageerror", (e) => errors.push(String(e))),
  );
  const page = await context.newPage();
  try {
    await page.goto(f.url + "/editor/");
    await page
      .getByRole("button", { name: "＋ 新建文章", exact: true })
      .waitFor();
    await page.getByLabel("文章标题", { exact: true }).fill("M1浏览器合成验收");
    await page
      .getByLabel("文章正文", { exact: true })
      .fill("# 标题😀\n\n第一段 **加粗**\n\n- 项目一");
    await page.getByRole("button", { name: "摘要、署名与更多信息" }).click();
    await page.getByLabel("摘要", { exact: true }).fill("合成摘要");
    await page.getByLabel("作者", { exact: true }).fill("合成作者");
    await page.getByRole("button", { name: "保存文章", exact: true }).click();
    await page.getByText("已保存为第 1 版", { exact: true }).waitFor();
    await page.getByRole("button", { name: "预览", exact: true }).click();
    await page.locator(".prose strong").filter({ hasText: "加粗" }).waitFor();
    await page.screenshot({
      path: path.join(f.output, "editor-desktop.png"),
      fullPage: true,
    });
    const article = (
      await (await context.request.get(f.url + "/api/v2/articles")).json()
    ).articles.find((a) => a.title === "M1浏览器合成验收");
    assert(article);
    const second = await context.newPage();
    await second.goto(f.url + "/editor/");
    await second
      .getByRole("button")
      .filter({ hasText: "M1浏览器合成验收" })
      .click();
    await second
      .getByLabel("文章正文", { exact: true })
      .fill("另一窗口的已保存正文");
    await second.getByRole("button", { name: "保存文章", exact: true }).click();
    await second.getByText("已保存为第 2 版", { exact: true }).waitFor();
    await page
      .getByLabel("文章正文", { exact: true })
      .fill("本页未保存的人工修改");
    await page.getByRole("button", { name: "保存文章", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "其他页面修改" }).waitFor();
    assert.equal(
      await page.getByLabel("文章正文", { exact: true }).inputValue(),
      "本页未保存的人工修改",
    );
    page.once("dialog", (d) => d.dismiss());
    await page
      .getByRole("button", { name: "＋ 新建文章", exact: true })
      .click();
    assert.equal(
      await page.getByLabel("文章正文", { exact: true }).inputValue(),
      "本页未保存的人工修改",
    );
    page.once("dialog", (d) => d.accept());
    await page
      .getByRole("button")
      .filter({ hasText: "M1浏览器合成验收" })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector(".body-input")?.value === "另一窗口的已保存正文",
    );
    let release, seen;
    const blocked = new Promise((r) => (release = r)),
      intercepted = new Promise((r) => (seen = r));
    await page.route("**/api/v2/articles/" + article.id, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const response = await route.fetch();
      seen();
      await blocked;
      await route.fulfill({ response });
    });
    await page.getByLabel("文章正文", { exact: true }).fill("请求已提交的正文");
    await page.getByRole("button", { name: "保存文章", exact: true }).click();
    await intercepted;
    await page
      .getByLabel("文章正文", { exact: true })
      .fill("请求之后继续输入的正文😀");
    release();
    await page
      .getByText("本次提交已保存；随后输入的内容仍未保存。", { exact: true })
      .waitFor();
    assert.equal(
      await page.getByLabel("文章正文", { exact: true }).inputValue(),
      "请求之后继续输入的正文😀",
    );
    await page.unroute("**/api/v2/articles/" + article.id);
    await page.waitForTimeout(5500);
    assert.equal(
      await page.getByLabel("文章正文", { exact: true }).inputValue(),
      "请求之后继续输入的正文😀",
    );
    await page.getByRole("button", { name: "保存文章", exact: true }).click();
    await page.getByText("已保存为第 4 版", { exact: true }).waitFor();
    await page.reload();
    await page
      .getByRole("button")
      .filter({ hasText: "M1浏览器合成验收" })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector(".body-input")?.value ===
        "请求之后继续输入的正文😀",
    );
    const compatibility = await context.newPage();
    await compatibility.goto(f.url + "/");
    await compatibility.locator("#nav").waitFor();
    assert(
      (await compatibility.locator("body").innerText()).includes("内容库"),
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(f.output, "editor-mobile.png"),
      fullPage: true,
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(f.output, "result.json"),
      JSON.stringify(
        {
          passed: true,
          checks: [
            "metadata_save_and_reopen",
            "markdown_preview",
            "two_window_conflict",
            "cancel_navigation",
            "save_while_typing",
            "background_poll_protection",
            "compatibility_ui_same_origin",
            "mobile_containment",
            "no_page_errors",
          ],
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
