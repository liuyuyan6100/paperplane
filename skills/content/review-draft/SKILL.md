---
name: review-draft
version: 1.1.0
description: 对已保存正文提出待确认的审稿建议
category: content
enabled: true
---

# 审稿

必填post（含body、title）及model_route；可选profile（字典或None）、instruction、structured。
提示词为prompts/review.md。结果suggestions是模型文本，reviewed_at使用studio.db.now模块函数。
ReviewManager传structured=true和既有JSON格式指令，再通过clean_result验证summary/issues及逐字引用。
保留原句匹配、事实核实确认、原稿版本检查和逐条采用机制；技能不自动采用建议。
最大正文200000字，超出拒绝而非静默截断；错误只返回安全提示，不记录原始上游异常。
未测真实模型建议质量，不声明成功率、耗时或全量覆盖率。
