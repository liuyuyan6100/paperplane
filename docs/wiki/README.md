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


当前 M2.4 复验重点：侧栏拖动调宽、收起后保留输入，以及独立弹窗阅读修改对照。步骤与证据继续维护在上方 M2 验收单/实施记录；用户体验待确认。
