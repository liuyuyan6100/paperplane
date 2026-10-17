# 开发测试

普通使用不需要 Node.js。以下开发回归在仓库根目录运行；建议先按根 README 建立独立虚拟环境，下面的 python 必须指向该环境。需要 Python 3.13、Node.js 22 和可下载 Chromium 的网络。

```sh
python -m pip install -r requirements-desktop.txt -r requirements-cloud.txt -r requirements-test.txt
npm ci
npx playwright install chromium
python -m pytest -q
python -m pytest tests/verify_f10.py -q
```

Windows 未激活虚拟环境时，将 python 替换为 .\.venv\Scripts\python.exe；PowerShell 调用 npm、npx 若遇脚本执行策略限制，可用 npm.cmd、npx.cmd。

pytest 会自动建立隔离 Studio 并运行五个浏览器集成场景，覆盖首次配置/使用引导、文章准备/送稿意图与插图位置，不需要私人素材、平台登录或开发者本机浏览器路径。单独运行 test_article_flow_browser.cjs、test_illustration_positions_browser.cjs 需要其 Python 测试创建的 fixture，通常直接运行 pytest 即可。

verify_usage_guide.cjs、verify_model_wizard.cjs、verify_model_setup.cjs 由 test_onboarding_browser.py 自动创建合成 fixture 后运行，检查引导入口、进度、保存/跳过、主动文字/图片自测和窄屏布局。无需另起服务或使用历史本机路径；不要直接运行缺少 fixture 参数的 .cjs。合成接口自测不证明真实供应商可用。

专项覆盖率命令（只统计指定模块）：

```sh
python -m pytest tests/test_skill_pipeline.py tests/test_skills.py tests/test_prompt_loader.py --cov=studio.skills --cov=studio.skill_executors --cov=studio.prompt_loader --cov=studio.sanitize --cov-report=term
```

test_public_documentation.py 会实际执行两个开发文档中的完整 Python 示例，检查公开 Markdown 的相对链接和风格包校验和。Windows 目录联接、文件符号链接会受权限影响；报告中跳过必须与通过分开说明。

所有模型响应和平台回执均使用合成测试边界；通过不代表真实模型质量、公众号权限或其他平台发表已验通。最近核验范围和未实测项目见 [公开文档核验表](../docs/VERIFICATION.md)。GitHub Actions 的每次运行单独记录结果。
