# 纸飞机与 Easel：应用场景和产品侧重

## 2026-10-23 上游更新与本轮同步

本轮通过官方Git远程核对上游`3f1d4fb8b99694f7231ee97163b637df89b50d3b`。本机Easel上次合入`b29d0d7`，本次按[这一范围的差异](https://github.com/ZJU-REAL/Easel/compare/b29d0d7...3f1d4fb)合入，保留本机定制。下方10月21日比较曾参考`8d08b21`，属于文档参考基线，不等于当时本机已安装版本。

| 本轮新增/修改 | 实际范围 | 纸飞机的取舍 |
| --- | --- | --- |
| 画像页浏览与切换已有persona | 复用已有角色入口，见[上游提交](https://github.com/ZJU-REAL/Easel/commit/91509a9) | 已有画像核心字段和文章关联，继续保持窄范围 |
| 视频合成检查点 | 输入及输出摘要一致才复用已验证镜头，失败保留原成片；限可安全重做的本地步骤，见[官方说明](https://github.com/ZJU-REAL/Easel/blob/3f1d4fb/docs/artifact-checkpoints.md) | 后续媒体迁入时参考，当前不扩展视频制作 |
| 可选原生工具预算 | 可设次数和总时限；默认关闭，限OpenClaw原生执行，不覆盖Codex/ACP，见[官方说明](https://github.com/ZJU-REAL/Easel/blob/3f1d4fb/agents/tool-budget/README.md) | 当前优先明确任务处理与恢复，不新增通用Agent调度层 |
| 多项可靠性修复 | 聊天流顺序及历史恢复、命名空间模型ID、BOM配置、发布状态和媒体脚本 | 保持未保存保护、失败可处理、平台未知结果先核对 |

本轮纸飞机M4.3.2获用户“初步验收通过”。新增侧栏“功能进度与规划”，下一小单元为图文检查与公众号草稿衔接，随后逐个平台迁入改编；资料/写作经验列P2，稳定升级与回退列M5。详见[Roadmap](../ROADMAP.md)。Easel更新和本机测试不代表真实模型、媒体服务或平台发布验收，也不代表纸飞机已实现表中Easel能力。

核对日期：2026-10-21。纸飞机以本仓库M4.2.2、已验收的P1.1和P1.2开发候选源码及[Roadmap](../ROADMAP.md)为依据；Easel以[官方README固定提交8d08b21](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/README.md)及[人工发布说明](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/docs/manual-publishing.md)为依据。对方介绍属于官方陈述，本轮没有操作对方账号或进行对照性能测试。

## 从实际任务区分

**纸飞机的中心对象是一篇持续修改的文章。** 你已有笔记、资料或实践经历，希望自己掌握提纲、段落、图片与采用决定；AI在文章旁参与讨论和改稿，任务、修订及媒体继续关联同一篇文章。技术实践、知识解释、个人公众号与博客长文是当前重点。

**Easel的官方主线是Agent参与的社媒运营。** 热点、选题、多种内容形态及运营结果串成连续工作流。[官方介绍](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/README.md)

| 维度 | 纸飞机当前侧重 | Easel官方侧重 |
| --- | --- | --- |
| 工作起点 | 从作者已有资料和一篇文章出发 | 从发现与策划进入创作 |
| 日常操作 | 编辑器、侧边AI、提纲/待审稿/图片逐步采用 | Agent、画像、技能与工具协作 |
| 内容交付 | 长文、封面、正文插图与平台稿记录 | 图文、音频、视频等多种成品 |
| 发布路径 | 新界面图文预览；送稿等原能力保留兼容入口 | 自动及人工审核发布路径 |
| 复盘位置 | 后续归并发表状态、真实指标和经验能力 | 运营归因属于官方主线 |

Easel列同时依据[官方README](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/README.md)和[人工发布说明](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/docs/manual-publishing.md)。这是关注点的映射，不是互斥的能力名单。

## 三个可以直接判断的场景

1. **“我写一篇实践文章，改了好几次，过两天还想接着改。”** 纸飞机优先围绕原稿、提纲、待审稿、段落修改、图片位置和历史版本组织工作，当前新界面主线已接通。
2. **“我希望Agent参与选题，并把主题做成多种社媒内容。”** Easel的官方工作流更集中展示这一用途；纸飞机保留部分相交能力，但剩余新界面迁入仍需按Roadmap完成。[Easel说明](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/README.md)
3. **“我只想在平台自己点发布。”** 两者均可有人工参与。Easel提供审核后复制/导出再到官网提交的路径；纸飞机当前围绕版本采用、图文预览与平台草稿状态，公众号公开发表在微信后台确认。[Easel人工路径](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/docs/manual-publishing.md)

## 共同点与当前边界

画像、素材、长文、多平台适配和经验沉淀都存在交集，不能写成纸飞机独有。Easel也提供Windows原生安装和Web工作台；本机使用及人工审核不是唯一分界。[安装与使用说明](https://github.com/ZJU-REAL/Easel/blob/8d08b216105bd7c7057c84c97cd637cec44db609/README.md)

纸飞机日常创作通过自选模型API，不要求使用者先登录厂商Agent；用户已确认P0/M4.2和P1.1画像入口测试通过，P1.2选题及入门/任务收纳已获2026-10-23用户初步验收。平台改编、送稿与部分运营能力保留原入口，不把“代码存在”写成“新工作台全部迁入”。也不把Easel简单描述为只能自动发布，或用未做过的实验声称速度、质量、成本、成功率更好。

## 致谢

感谢[ZJU-REAL/Easel](https://github.com/ZJU-REAL/Easel)提供的工作台、画像/素材/技能连接和首次模型配置引导灵感。Easel是独立项目；纸飞机的MIT声明不改变对方及其依赖的许可，详见[NOTICE](../NOTICE.md)。
