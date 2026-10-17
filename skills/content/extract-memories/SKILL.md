---
name: extract-memories
version: 1.0.0
description: 从明确选择的来源中提炼待确认的画像经验
category: content
enabled: true
---

# 经验提炼

接收post、profile、model_route及instruction；路由task沿既有配置使用chat。
来源快照保存在post.body，不扫描对话或追加画像；调用方显式传profile=None。
返回raw，memories.extract继续校验最多五条候选、原句逐字匹配及平台范围；全部校验后才保存候选。
候选不自动确认，不直接影响后续召回。技能不执行数据库写入或后台挖掘。
失败不经旧实现重发；不新增模型配置项。
