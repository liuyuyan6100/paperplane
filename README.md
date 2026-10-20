# 纸飞机 PaperPlane

面向个人作者的 AI 图文工作台。在 Windows 本机整理资料、接入自己的模型 API，围绕一篇文章完成提纲确认、正文修改、配图和草稿准备，并保留每次采用的版本与任务记录。

[功能与 Roadmap](ROADMAP.md) · [操作与测试指南](docs/feature-guides/README.md) · [应用框架](docs/diagrams/README.md) · [与 Easel 的场景对比](docs/PROJECT_COMPARISON.md)

作者：**Aiclaw** · [个人博客](https://blog.aiclawonline.website/) · [微信公众号：沐沐ai专题](#微信公众号)

[下载 Windows 测试预发布版 M4.2.2](https://github.com/liuyuyan6100/paperplane/releases/tag/v0.4.0-m4.2.2) · 安装版本 `0.4.0.6` · [更新说明](docs/releases/v0.4.0-m4.2.2.md)

## 适合怎样的创作

- **技术实践与知识长文**：从自己的笔记、参考资料和实际经历出发，先确认结构，再写正文；让 AI 讨论或修改指定段落，作者审阅后采用。
- **持续经营个人账号**：复用账号定位、读者和写作风格；文章、封面、正文插图与修改历史放在一起，隔几天回来仍能接着做。
- **公众号与博客图文准备**：从母稿逐步准备成品，核对图文效果和平台草稿状态；平台适配、送稿和复盘按各自的实际支持范围使用。

创作主线是：**保存文章 → 选择素材与画像 → 确认提纲 → 审阅并采用正文 → 制作封面与插图 → 图文成品预览**。新编辑器已经接通这条主线；公众号送稿和部分平台功能仍从原工作台进入。

## 当前做到了哪里

截至 **2026-10-21**，最新公开测试预发布版为 **`0.4.0-m4.2.2`**，Windows 安装版本 `0.4.0.6`；P1.1画像入口已由用户确认测试通过，当前源码正在验证P1.2选题策划入口`0.4.0-m4.3.2`。当前分支是仓库开发基线，仍处于架构整理阶段，M5稳定交付尚未完成。历史`0.1.76`是本轮整理的公开起点。

| 状态 | 已有内容 | 证据或入口 |
| --- | --- | --- |
| 已验收 · M1 | 文章新建、Markdown编辑、保存重开、版本冲突与未保存保护 | [文章编辑](docs/architecture/milestones/01-editor-acceptance.md) |
| 已验收 · M2 | 侧边AI讨论、全文/选区修改、对照、采用与撤销、历史建议继续修改 | [侧边AI](docs/architecture/milestones/02-assistant-acceptance.md) |
| 已验收 · M3 | 任务中心、本篇筛选、失败/中断恢复、未知送稿先核对 | [任务中心](docs/architecture/milestones/03-tasks-acceptance.md) |
| 已验收 · M4.1 | 素材→可编辑提纲→确认后生成待审正文→采用保存；关闭习惯与文章归档/回收站 | [完整练习](docs/feature-guides/m4.1-text-creation.md) |
| 已验收 · M4.2 | 封面与正文插图计划、生成/上传、位置预览、采用保存、独立图文预览、回收站永久删除、失败生成不占候选卡片 | [配图与管理操作](docs/feature-guides/m4.2-images.md) |
| 已验收 · M4.3.1 | 新编辑器管理画像名称、内容方向、目标读者和运营目标 | [画像操作指南](docs/feature-guides/m4.3.1-profiles.md) |
| 开发候选 · M4.3.2 | 一段想法与画像生成三个候选选题，人工选择后创建空白文章 | [选题操作指南](docs/feature-guides/m4.3.2-topics.md) |
| 兼容入口保留 | 画像与已确认经验、选题、平台改编、送稿准备及回执、已有媒体、备份恢复等原功能 | [功能保留清单](docs/architecture/feature-parity.md) |
| 规划中 | 剩余能力逐项迁入、统一数据访问与迁移、完整升级/回退、正式Windows稳定交付 | [Roadmap](ROADMAP.md) |

“代码存在”“已迁入新界面”“自动测试通过”和“用户验收”分开记录。平台目录也不代表每个平台都已真实验通。

## 应用框架

![纸飞机当前应用框架](docs/diagrams/paperplane-framework.svg)

框架图对应当前源码，附[源图、可交互HTML与校验记录](docs/diagrams/README.md)。GitHub中展示静态SVG；下载HTML后可在浏览器中查看节点来源、切换主题并导出图片。

当前采用 **React + TypeScript、FastAPI、Python模块化单体、SQLite，以及Windows WebView2桌面壳**。新功能通过窄接口复用原业务与唯一后台worker；本地草稿不被后台刷新覆盖，生成结果经审阅采用后才更新原稿。SQLAlchemy/Alembic等数据访问整理仍是后续工作，图中不把规划当成已接入。

## Roadmap

| 阶段 | 目标 | 当前进度 |
| --- | --- | --- |
| M0–M3 | 框架基线、文章编辑、侧边AI与任务恢复 | M0完成；M1–M3已用户验收 |
| M4.1 | 打通素材、提纲确认与正文采用 | 已用户验收 |
| M4.2 | 打通封面、正文配图与图文预览 | 用户已确认P0完成；单项证据见原记录 |
| M4.3.1 / P1.1 | 把画像四个核心字段接入新编辑器 | 用户确认测试通过 |
| M4.3.2 / P1.2 | 在新编辑器中生成、比较并人工选定选题 | 开发方回归与成品核验通过，待用户安装验收 |
| M4其余单元 | 画像其他字段/经验、选题/改编、媒体/平台/复盘的逐项迁入；数据访问及完整兼容核对 | 保留原入口，分组推进 |
| M5 | 稳定安装升级、正式数据迁移及回退、完整Windows使用链 | 规划中，需独立验收 |

完整功能分组、实现边界和完成条件见[ROADMAP.md](ROADMAP.md)。不预设交付日期，也不把当前开发基线切换等同于稳定版发布。

## 与 Easel 的应用场景有什么不同

纸飞机优先服务**围绕一篇文章反复编辑、核对与采用的个人作者**；Easel官方主线是**由Agent串联社媒选题、多媒体制作、发布和复盘**。[Easel官方介绍](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/README.md)

| 你的任务 | 纸飞机当前侧重 | Easel官方侧重 |
| --- | --- | --- |
| 已有资料，要逐节完成长文 | 提纲确认、待审稿、编辑旁修改、修订与配图采用 | Agent与内容技能协作 |
| 从社媒选题扩展多种成品 | 平台改编等原能力保留，新界面继续迁入 | 发现、策划、多媒体制作与运营 |
| 自己核对后提交平台 | 图文预览与草稿回执；公众号公开发表在微信确认 | 自动与人工审核发布路径均提供 |

Easel同样支持画像、长文、Windows/Web和人工审核发布，这些不是纸飞机独有能力。对比是使用重点与当前交付进度，详见[场景、边界和来源](docs/PROJECT_COMPARISON.md)。感谢Easel带来的工作台与模型配置引导灵感。

## 从源码体验当前工作台

开发构建需要 **Python 3.13、Node.js 22、Git**。建议先建立虚拟环境；以下在Windows PowerShell执行：

```powershell
git clone https://github.com/liuyuyan6100/paperplane.git
cd paperplane
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-preview.txt
npm ci --prefix frontend
npm run build --prefix frontend
.\.venv\Scripts\python.exe preview.py --serve --open --data-dir ./data-preview
```

使用终端打印的本机地址，按[功能指南](docs/feature-guides/README.md)保存一篇测试文章，再配置自己的文字/图片模型API。没有配置模型时也可手写和保存文章。源码示例使用独立`data-preview/`，不导入开发者的数据或配置。

Windows普通安装包从源码构建：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_preview.ps1 -OpenInstaller
```

构建还需要Inno Setup 6；普通安装后运行不要求使用者另外安装Node.js。验证版数据为`%LOCALAPPDATA%/PaperPlaneStudioPreview/local`，原正式版数据为`%LOCALAPPDATA%/PaperPlaneStudio/local`，两者独立；当前不自动把正式数据迁入验证版。

<details>
<summary>原Web工作台与原桌面构建入口</summary>

原功能通过兼容入口继续保留。单独启动原Web工作台：

```sh
python -m pip install -r requirements.txt
python run.py --data-dir ./data --open
```

默认访问`http://127.0.0.1:8787`；端口占用可加`--port 8788`。原桌面构建使用`tools/build_desktop.ps1`；部署、服务器认证和完整运行方式见[部署说明](docs/DEPLOYMENT.md)。这些入口与当前React工作台应按对应版本使用。

</details>

## 数据、平台与验证

文章、修订、任务和回执保存在SQLite，图片/视频保存在所选数据目录；Windows凭据使用当前用户的DPAPI加密。[数据与备份说明](docs/DATA_AND_SECURITY.md)

公众号接口需要对应权限；送入草稿箱后在微信后台核对并公开发表。公众号原生图文视频仍需按对应流程人工上传和关联，API菜单/回复视频素材不能冒充图文视频。本机与服务器的文章、素材各自保存，当前不自动同步。其他平台的适配与真实可用性以各自回执和验收为准。

最近一次应用回归：**1162通过、6环境跳过、0失败，另358个子测试通过**；前端16项，成品40模块及71资源核验通过。该结果不等于所有外部平台或生成质量均已验收，详见[M4.2.2实施与验证](docs/architecture/milestones/04-images-implementation.md)。开发方式与依赖见[测试说明](tests/README.md)，历史文档核验见[核验表](docs/VERIFICATION.md)。

目录：`frontend/`当前React工作台，`studio/`后端与业务适配，`web/`兼容前端，`library/`共享资料库服务，`skills/`与`agents/`内容技能和提示词，`tests/`隔离测试，`tools/`构建与维护工具。

问题和建议请提交到[Issues](https://github.com/liuyuyan6100/paperplane/issues)，使用合成稿并移除Key、Cookie、个人文章与运行数据库。

## 微信公众号

作者：**Aiclaw** · 微信公众号：**沐沐ai专题**

扫码阅读 AI 学习笔记、工程实践与项目更新。二维码取自作者博客的“联系与订阅”。

<a href="docs/assets/wechat-official-account.jpg"><img src="docs/assets/wechat-official-account.jpg" alt="沐沐ai专题 微信公众号二维码" width="192" /></a>

## 许可证与致谢

原创代码采用[MIT](LICENSE)；第三方依赖保留各自许可。感谢[Easel](https://github.com/ZJU-REAL/Easel)的产品灵感，详见[第三方说明](NOTICE.md)。
