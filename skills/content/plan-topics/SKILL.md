---
name: plan-topics
version: 1.0.0
description: 基于冻结画像及所选资料提出下一篇候选选题
category: content
enabled: true
---

# 候选选题

接收post、profile、model_route及instruction；路由task必须为topic。
复用TopicManager现有提示词和快照，不重复添加提示词或访问更多资料。
通过统一模型接口请求结构化结果，返回raw；TopicManager继续校验恰好三个候选及字段限制。
不创建文章或确认选题；用户选择后才由既有事务创建空正文选题。
失败不经旧实现重发；模型来源记录及临时重试沿用Studio.generate_draft。
