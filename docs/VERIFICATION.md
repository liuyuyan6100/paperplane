# 公开文档核验记录

核验日期：2026-10-18；候选版本：0.1.76。环境：Windows 11、Python 3.13.3 x64、Node.js 24.13.0（GitHub CI 使用 Node.js 22）、Docker Desktop Linux/amd64。使用公开快照、全新虚拟环境与独立合成数据，不读取使用者正式数据库和平台凭据。

“本地回归”表示运行真实应用代码，但模型响应、平台回执使用合成边界；它不等同于外部账号实测。自动回归结果以对应提交的 GitHub Actions 为准；本地验证与外部服务边界如下。

| 文档 / 步骤 | 本轮核验范围 | 当前边界 |
|---|---|---|
| README：公开 clone、基础 pip 安装、run.py --data-dir --open | 已以匿名公开仓库、新虚拟环境实际启动并打开首次配置页；再次检查候选版 | macOS/Linux 原生 Python 安装未实测 |
| README / DEPLOYMENT：Windows 文件夹构建 | 全新 .venv 实际打包；检查冻结程序自检及资源 | WebView2 桌面窗口的全新系统依赖需用户环境验证 |
| README / DEPLOYMENT：Inno Setup 安装包 | 实际编译并核对校验和 | 未在干净 Windows VM 中点完安装、升级与卸载；不声称验通 |
| DEPLOYMENT：主 Docker Compose | 实际构建 Linux/amd64 镜像、启动、健康检查、持久化、设置/文章路由、资源校验及拒绝未认证请求 | 真实 Cloudflare 登录、域名/TLS、ARM 镜像未实测 |
| DATA_AND_SECURITY：备份 / CLI 恢复 | 临时数据完整备份、校验与恢复；测试防重放 | 跨电脑 DPAPI 凭据不可迁移；真实旧版备份兼容性未穷举 |
| USER_GUIDE：画像、素材、提纲、正文采用、侧边改稿、图文准备 | 本地合成回归；文章准备与配图位置有 Chromium 集成测试 | 真实模型内容质量、费用、限流与服务兼容性需配置后自测 |
| USER_GUIDE：本地视频导入与位置 | 本地导入、文章引用和发送前拦截回归 | 微信原生视频上传、转码、原创声明及账号权限本轮未实测 |
| USER_GUIDE：公众号草稿 / 其他平台 / 公开发表 | 合成回执的版本、防重复、待核对分支 | 真实账号送稿、审核、公开发表未执行；不可当作全平台验通 |
| tests/README：安装依赖、pytest、额外契约、专项覆盖率 | 干净环境执行；浏览器依赖来自本仓库 npm lock | 历史 verify_*.cjs 专用手动探针未包含在完整回归中 |
| agents/README、MEMORIES | 两段完整 Python 示例直接运行；不依赖未定义变量或私人数据 | 模型自动抽取质量未调用真实模型验证 |
| skills/README、SPEC、各 SKILL.md 与 prompts | 检查资源与协议，技能测试覆盖 | SKILL / prompts、PERSONA / WORKFLOWS 是模型输入模板，不是终端操作步骤 |
| visual-styles 风格包 | 校验文件与 manifest，检查 Windows 与 Docker 资源 | 当前 Easel 上游导入与真实生图效果未实测；历史集成路径仅供参考 |
| PROJECT_COMPARISON、NOTICE、LICENSE | 核对定位、来源链接、MIT 与第三方边界 | Easel 功能来自其官方介绍，未做速度/质量对照实验 |
| AGENTS.md | 贡献者约束，与运行配置区分 | 不是应用自动读取的长期写作规则 |

## 本轮修复

- Windows PowerShell 5 首次打包缺依赖时不再被 traceback 提前终止，会正常安装固定版本依赖。
- Docker 构建上下文放行必要技能、提示词和视觉资源，补齐版本模块；验证设置、文章、技能与资源，避免 COPY 失败或服务存活但业务接口报错。
- 浏览器回归改用公开 npm 固定版本 Playwright 和下载的 Chromium，移除开发机路径。
- 修正 Windows runner 短路径导致的本地图片拒绝与路径断言；保持不可信 UNC / 越界路径拦截。
- 明确文本 UTF-8 编码，清理不存在的文档链接、伪 API 示例和未实测性能数字。

后续每个提交的 CI 结果见 [GitHub Actions](https://github.com/liuyuyan6100/paperplane/actions)。通过一次核验不保证所有电脑、模型供应商或平台账号都适用；报告问题请附脱敏环境、命令和错误，不要提交密钥或个人数据库。
