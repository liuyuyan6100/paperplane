# 纸飞机 Wiki

本入口索引项目的使用说明、架构决定与阶段验收。正文保存在对应权威文件中，随同一 Git 提交演进；不维护另一份重复的架构或状态说明。

## 使用与学习

- [使用指南](../USER_GUIDE.md)：素材、画像、提纲、正文、侧边改稿与公众号草稿。
- [部署和安装](../DEPLOYMENT.md)：当前公开版的运行方式；架构验证版另按阶段交付。
- [数据与安全](../DATA_AND_SECURITY.md)：数据位置、凭据和公开边界。
- [原公开版核验范围](../VERIFICATION.md)：历史已验证与未实测项目。

## 架构验证

- [架构总入口](../architecture/README.md)
- [框架与边界](../architecture/0001-framework-and-boundaries.md)
- [阶段计划](../architecture/migration-plan.md)
- [功能保留清单](../architecture/feature-parity.md)
- [回归与交付](../architecture/validation-and-release.md)
- [M0 框架基线](../architecture/milestones/00-foundation.md)
- [M1 文章编辑验收](../architecture/milestones/01-editor-acceptance.md)
- [M1 实现与交付](../architecture/milestones/01-editor-implementation.md)
- [M2 侧边 AI 安装验收](../architecture/milestones/02-assistant-acceptance.md)
- [M2 实现与配置复用](../architecture/milestones/02-assistant-implementation.md)

## 每轮更新什么

实现前写范围和预期；交付前补回归与安装包事实；安装后补运行核验；用户体验后补反馈、修复与确认。未知项保持未知，实际失败保留记录。这里是仓库内 Wiki；没有声称独立 GitHub Wiki 或其他云端知识库已同步。


历史 M2.4 复验重点：侧栏拖动调宽、收起后保留输入，以及独立弹窗阅读修改对照。步骤与证据维护在上方 M2 验收单/实施记录；该轮问题及后续M2验收结论均保留。


M2.5补充：历史建议通过“基于当前稿继续修改”承接到新请求，当前正文与旧快照各自保留；具体步骤与交付证据继续维护在M2验收单/实施记录。


## M3任务状态

M2.5用户已确认当前验证无问题，M2验收通过。后续操作沿用[M3安装与逐步验证](../architecture/milestones/03-tasks-acceptance.md)，模块、恢复规则和回归证据见[M3实施记录](../architecture/milestones/03-tasks-implementation.md)。现有文章与配置保留，2026-10-19用户明确确认M3测试通过，M1–M3已验收。后续M4首条创作链已按用户授权实施，见下方M4.1记录。


2026-10-19 M3.2补充：文章信息区提供AI补齐空白摘要/署名/标签，先审阅再填入，保留手动信息；任务中心增加用途解释及本篇状态入口。M3现已用户确认通过，详见M3验收单。


## M4.1 素材到正文采用

M3已由用户明确验收，M4首轮按[素材、提纲与正文采用实施记录](../architecture/milestones/04-creation-implementation.md)推进。[M4.1安装与逐步验证](../architecture/milestones/04-creation-acceptance.md)说明如何选择素材、确认提纲、审阅正文和处理采用冲突。原功能与数据保留；本轮完整回归、成品、真实模型合成链与升级回退均已通过，0.4.0.1候选安装和用户体验待确认，其他M4能力尚未整体迁移。
