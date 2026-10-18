# 纸飞机 PaperPlane

一个面向个人创作者的 AI 内容工作台，把素材、账号画像、提纲、正文修改、配图、平台改编和送稿记录放在同一处。

本分支为 **架构验证分支**。M1、M2已由用户安装验收通过，当前M3候选 `0.3.0-m3.2` 增加任务中心：查询状态、按原任务恢复、保留确认点和未知送稿防重复保护。保留文章编辑、侧边AI、原数据与配置，固定名称覆盖安装。[M3实施记录](docs/architecture/milestones/03-tasks-implementation.md)与[验收步骤](docs/architecture/milestones/03-tasks-acceptance.md)记录交付状态；M3用户验收待确认，M4未开始。参见[架构计划](docs/architecture/README.md)与[项目Wiki](docs/wiki/README.md)。下方原有启动命令继续打开兼容工作台。

验证新编辑器需安装 `requirements-preview.txt`，在 `frontend/` 执行 `npm ci`、`npm run build`，再在仓库根运行 `python preview.py --serve --open --data-dir ./data-preview`。Windows 独立安装包由 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build_preview.ps1` 构建。验证版默认使用 `%LOCALAPPDATA%/PaperPlaneStudioPreview/local`，与正式版独立。

当前开源快照：**0.1.76，早期版本，持续改进中**。适合希望在自己的电脑上管理文章，并接入自己的模型 API 的创作者与开发者。

## 可以用来做什么

- **整理素材**：导入文章、笔记和图片，按项目组织资料；共享资料库支持文件夹导入。
- **保存账号画像**：管理定位、读者、写作风格和文末署名；创作任务携带画像和已确认经验。
- **逐步写作**：生成提纲，确认后写正文；候选正文审阅采用后更新原稿。
- **在文章旁修改**：围绕当前工作项与 AI 对话，查看建议或改稿并采用；版本检查避免静默覆盖后续手动修改。
- **准备图文**：规划封面和正文插图、审阅候选、采用配图，或导入自己的图片和视频素材。
- **改编与送稿**：保留母稿，生成不同平台的版本，执行检查并记录草稿回执；结果未知时先核对，避免盲目重发。
- **本机使用**：Windows 桌面客户端，或 Python 启动本地 Web 工作台；模型地址、Key 和模型名自行配置。

这些能力仍有体验与平台兼容性限制，不能把平台目录中的每一项都理解成已经全自动验通。

## 从源码运行

需要 Git 和 Python 3.13（建议 64 位），并能访问 GitHub 与 Python 包仓库。本地 Web 服务需要 Pillow 与 PyYAML；Windows 桌面和云端访问另有依赖。

```sh
git clone https://github.com/liuyuyan6100/paperplane.git
cd paperplane
python -m pip install -r requirements.txt
python run.py --data-dir ./data --open
```

访问 `http://127.0.0.1:8787`。服务默认仅监听本机；关闭浏览器不等于停止服务，在终端按 Ctrl+C 停止。首次使用无需导入任何开发者的配置或数据。

以上四步已用公开仓库的独立环境验证。推荐先创建虚拟环境，避免影响其他项目：Windows 用 `python -m venv .venv`，后续将 python 换为 `.\.venv\Scripts\python.exe`；macOS/Linux 用 `python3 -m venv .venv` 和 `.venv/bin/python`（原生 macOS/Linux 安装本轮未实测）。端口占用时可追加 `--port 8788`，并访问对应端口。clone 提示目录已存在时，请先切到空目录，不要覆盖已有数据。

打开「使用引导」，或阅读 [使用指南](docs/USER_GUIDE.md)：配置模型 → 连接公众号 → 确认提纲与正文 → 准备图文 → 检查并送草稿 → 微信后台确认发表。

## Windows 客户端

在 Windows PowerShell 中从源码构建；安装向导由使用者确认：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_desktop.ps1 -OpenInstaller
```

需要 Python 和 Inno Setup 6。脚本会在 `.venv` 安装桌面构建依赖，产物默认写入 `dist/`。没有 Inno Setup 时可用 `-SkipInstaller` 仅生成可运行目录。

安装版业务数据位于 `%LOCALAPPDATA%\PaperPlaneStudio\local`；源码版默认使用项目 `data/`，也可显式传入 `--data-dir`。两者不会自动合并。

## 平台能力与当前边界

- 微信公众号支持官方 API 图文草稿流程，需要账号对应接口权限和正确的 IP 白名单；**送入草稿箱不等于公开发表**。
- 当前观察到的 API 视频素材用于菜单、自动回复等场景，不能据此认定图文视频可用。公众号正文视频需在微信图文编辑器「本地上传」并插入保存，再关联原生视频；当前版本会拦截不适用的 API 视频素材。
- 其他平台包含不同程度的登录、导出、图文草稿或浏览器适配能力。以界面中的具体能力和实际回执为准，不能承诺所有账号都可用。
- 本机和服务器的文章、素材各自保存，不自动同步。云端部署需配置访问认证，见 [部署说明](docs/DEPLOYMENT.md)。
- AI 生成仍需核实事实与图片。模型调用与平台操作可能产生费用或依赖外部服务，不包含免费额度。

## 数据与开发

文章、修订、任务与回执保存在 SQLite；图片和视频保存在所选数据目录。Windows 凭据使用当前系统用户的 DPAPI 加密。备份与跨机恢复的边界见 [数据说明](docs/DATA_AND_SECURITY.md)。

```sh
python -m pip install -r requirements-desktop.txt -r requirements-cloud.txt -r requirements-test.txt
npm ci
npx playwright install chromium
python -m pytest -q
```

完整回归还需要 Node.js 22 和 Chromium；具体说明见 [开发测试](tests/README.md)。所有公开文档步骤的核验范围、已修复问题和未实测项目见 [核验表](docs/VERIFICATION.md)，请先阅读与你运行方式对应的一行。

目录：`studio/` 后端，`web/` 前端，`library/` 共享资料库服务，`skills/` 内容技能，`agents/` 助手提示词，`tests/` 合成测试，`tools/` 通用构建与维护工具。

欢迎通过 [Issues](https://github.com/liuyuyan6100/paperplane/issues) 反馈可复现的问题。请使用合成数据，移除 Key、Cookie、个人文章和运行数据库后再提交。

## 感谢 Easel

感谢 [ZJU-REAL/Easel](https://github.com/ZJU-REAL/Easel) 带来的灵感，尤其是将账号画像、内容技能、素材和发布流程连接起来的工作台思路，以及首次模型配置引导的交互启发。

Easel 的官方介绍强调以 OpenClaw Agent 串联发现、策划、创作、发布和归因，并提供丰富的图像、音频与视频技能。纸飞机当前更聚焦个人文章创作：在 Windows 本机接入自选模型 API，围绕提纲确认、编辑旁对话、版本采用和公众号草稿准备组织工作。双方都涉及画像、素材与平台适配；这里比较的是定位与使用侧重，不是性能排名，也不把对方的官方介绍冒充本项目实测。

详见 [项目定位对比与写作素材](docs/PROJECT_COMPARISON.md)。纸飞机无需先安装 OpenClaw；Easel 的当前要求与能力请以其仓库说明为准。

## 许可证

本项目原创代码采用 [MIT](LICENSE) 许可证。第三方依赖保留各自的许可证，参见 [致谢与第三方说明](NOTICE.md)。


2026-10-19 M3.2补充：文章信息区提供AI补齐空白摘要/署名/标签，先审阅再填入，保留手动信息；任务中心增加用途解释及本篇状态入口。M3用户验收仍待确认，详见M3验收单。
