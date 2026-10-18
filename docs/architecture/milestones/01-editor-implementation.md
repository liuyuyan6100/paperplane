# M1 文章编辑实现与交付

日期：2026-10-18。候选版本：`0.2.0-m1.1`，Windows 安装版本 `0.2.0.1`。文章编辑链路已实现，正式安装与用户验收尚待确认；M2 未开始。

## 本轮可以操作什么

文章列表、搜索、新建、标题/正文、阶段与画像选择、摘要/作者/标签/封面/来源、Markdown 预览、保存和重新打开。未保存离开会提醒；版本冲突保留本地输入，可下载当前稿。保存期间继续输入，回执只确认已提交的内容。

左下角“打开原工作台”连接同一验证库；原界面右下角可返回 M1 编辑器。验证版信息展开后可另开浏览器窗口核对冲突。新界面的侧边 AI 与任务中心留到后续阶段，现有相关业务仍可从兼容入口访问。

## 框架与运行关系

`frontend/src/features/editor/session.ts` 管理本地草稿、已保存基准、编辑会话、修改序号与保存请求。`App.tsx` 组装界面，TanStack Query 查询缓存不会自动写回编辑区。读回和保存结果必须匹配原编辑会话；保存时仍核对修改序号。

`studio/api/articles.py` 提供 Pydantic 输入契约和薄路由，调用 `ArticleService` 与 `ArticleRepository`。M1 使用 `LegacyArticleRepository` 包装原 Studio 的原子保存、版本检查、修订与画像学习写入。SQLite 表结构没有变化，SQLAlchemy/Alembic 尚未接管。

`PreviewService` 装配唯一 Studio 和 worker，另启动 FastAPI/Uvicorn 接口与原 HTTP 兼容适配器。新旧界面从同一本机访问地址使用同一业务服务，兼容适配器没有第二套 worker。新接口在 `/api/v2/`，新界面在 `/editor/`，原工作台保留在 `/`。

接口边界保留 Host、Origin、跨站、会话令牌、JSON 类型、请求大小、资源路径和 CSP 检查。兼容请求在边界检查后转到内部 loopback 服务，使用原服务会话令牌；关闭/稳定版更新接口在验证端拒绝。

## 安装与数据边界

可执行程序为 `PaperPlaneStudioPreview.exe`；独立 AppId、互斥量和安装目录 `%LOCALAPPDATA%/Programs/PaperPlaneStudioPreview`。安装器固定在此目录，拒绝用命令参数覆盖到其他目录；不执行稳定版清理脚本。

默认数据为 `%LOCALAPPDATA%/PaperPlaneStudioPreview/local`，浏览器登录态也在验证目录下。程序拒绝打开正式 `PaperPlaneStudio` 数据根及其子目录。首期开空库，可主动创建验收样稿；不复制正式文章、凭据或待执行任务。

Windows 验证壳提供新旧工作台、托盘继续运行和退出时未保存检查。当前验证壳仅连接本机，不提供稳定版远端切换、开机启动或自动更新；这些功能在稳定版继续保留，后续按完整兼容阶段处理。关闭选择本轮每次询问，未写入稳定版偏好。

## 自动验证

- 前端 TypeScript、生产构建、ESLint 与 5 项 reducer 状态测试通过。
- 首轮 Python/浏览器专项：12 通过、1 失败。失败为测试误用不存在的旧单篇 GET 接口，已改为真实 `/api/state` 内容对照；未放宽业务断言。
- 修正后 13 项专项通过，覆盖新旧入口同库、原稿/修订保留、过期版本拒绝、Host/Origin/令牌、JSON/CSP、超限 chunked 请求、路径边界、重启与唯一写入锁，以及真实 Chromium 浏览器流程。
- 浏览器覆盖多窗口冲突、取消切换、保存期间继续输入、后台轮询不覆盖、保存重开、原工作台和窄屏布局；没有页面脚本错误。
- 首次前端构建的重复字段与遗留 CSS 引用错误已修复；首次 lint 的配置/导入问题保留原始输出，后续检查通过。

完整回归 **1064 通过、6 跳过、0 失败**，另 335 个子测试通过，用时 671.81 秒。6 项跳过为 3 项 Windows 符号链接权限条件、3 项本机未安装 Gitleaks 的检查；安全 CI 独立执行后者。完整回归开始后补正了新建文章的默认阶段，最终 13 项专项再次通过，旧业务代码没有修改。

最终冻结包的入口、13 个关键 Python 模块与 71 份资源和当前源码一致；成品服务真实启动、保存、原界面读回、拒绝无效令牌、重启后保存内容保留、SQLite 完整性检查通过。首轮成品验证在临时库清理时遇到 WinError 32；当时占用者未能事后确认。验证工具现根据冻结运行端报告的 PID 结束本次进程并保留合成证据，最终验证通过，首次错误记录保留。

原始日志保留于本地 `test-results/m1-*.log` 与 XML，成品报告为 `m1-package-final.json`，交付索引为 `m1-release.json`，不包含在源码发布中。合成测试不等于真实模型、平台权限或微信播放验证；本轮未调用模型或写入真实平台。

## 候选安装包

文件名：`PaperPlaneStudioPreview-M1-Setup-0.2.0.1.exe`，大小 31,342,533 字节。SHA256：`6a72eca2e1b9f616665f07434a8a70807b71453b041c8786983ce7a08a135893`。本轮仅交付正常安装向导，由使用者完成安装；安装成功与体验确认单独登记。GitHub Release 尚未发布。

## 如何复现

Windows PowerShell，仓库根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-preview.txt -r requirements-cloud.txt -r requirements-preview-test.txt
npm.cmd ci
npx.cmd playwright install chromium
npm.cmd ci --prefix frontend
npm.cmd run build --prefix frontend
.\.venv\Scripts\python.exe -m pytest tests/test_preview_editor.py -q
.\.venv\Scripts\python.exe preview.py --serve --open --data-dir ./data-preview
```

构建独立安装包：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build_preview.ps1`。普通使用者安装成品后无需 Node.js 或 Python。

## 用户验收

按 [M1 安装验收单](01-editor-acceptance.md) 操作。安装向导打开不等于安装完成，自动测试通过不等于用户验收。只有明确确认通过后才开始 M2。


2026-10-18 用户确认：已安装并反馈“这几步验证没问题，继续做 M2”，本阶段用户验收通过。M1 名称只作为历史记录，M2 起固定为“纸飞机架构验证版”，同 AppId 覆盖升级。
