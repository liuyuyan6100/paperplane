# M0 框架与验收基线

日期：2026-10-18。范围：框架定版、独立分支、文档和现有行为基线；没有新增运行代码、安装或正式数据操作。

## 已完成的设计

- 明确 React/TypeScript/Vite、FastAPI/Pydantic、SQLite/SQLAlchemy/Alembic、持久化任务与原 Windows 桌面壳的分层职责。
- 按 M1 文章编辑、M2 侧边 AI、M3 任务状态安排安装验收，随后做完整兼容与稳定交付。
- 建立功能保留清单，单独登记较新本地候选尚未进入公开分支的恢复、采集、媒体与视频功能。
- 明确独立验证版安装和数据隔离，以及每阶段用户确认后继续的边界。

## 源码基线

基于公开主分支 `4b35c6d4595ffd25f0e955d626ce99ce47c52545`，分支名 `codex/architecture-editor-foundation`。应用源码版本仍为 0.1.76。框架方向已选定不代表新框架已实现或具有可安装版本。

## 验证记录

本地 Windows / Python 3.13，复用已有桌面与测试依赖环境，在新分支检出目录执行 `npm ci` 和完整 pytest。所有业务测试使用隔离合成数据；本轮没有宣称重新验证全新 Python 环境安装。

| 检查 | 结果 |
| --- | --- |
| 完整 pytest | 1051 通过、6 跳过、0 失败；另 333 个子测试通过，用时 624.81 秒 |
| 跳过原因 | 3 项 Windows 符号链接权限条件；3 项本机未安装 Gitleaks，需由现有安全 CI 验证 |
| 新文档相对链接 | 10 个 Markdown 文件，0 个断链 |
| 工作区公开源码检查 | 329 个文件，0 个规则命中；该检查不等于完整秘密扫描 |
| 起点可达历史检查 | 373 个文件/历史 blob 路径，0 个规则命中 |
| Git 补丁格式 | `git diff --check` 通过 |
| 依赖准备 | `npm ci` 成功，npm 报告 0 个已知漏洞；没有安装新的架构运行依赖 |

完整回归命令为 `python -m pytest -q -o faulthandler_timeout=90 --junitxml=test-results/architecture-baseline.xml`。原始日志为 Git 忽略的 `architecture-baseline.log`，JUnit XML 与本机证据索引位于 `test-results/`。子测试不与父测试相加。GitHub 测试和安全扫描结果按当前提交的 [Actions](https://github.com/liuyuyan6100/paperplane/actions) 单独查看，本地结果不代替 CI 结论。

## 用户验收

本阶段没有安装包，也没有用户安装确认。下一交付单元是 [M1 文章编辑](01-editor-acceptance.md)，必须形成可操作安装包后再请使用者验收。
