---
name: expand-section
version: 1.1.0
description: 正文扩写
category: content
enabled: true
---

# 正文扩写

输入：post、materials（最多六份已选择快照）、model_route、outline、confirmed=true。
可选：profile（允许None）、instruction、section（必须出现在已确认提纲中）、style（main/creative/professional）。
返回：content、word_count、materials_used。没有section时生成完整正文。
既有WorkflowManager负责提纲确认、原稿版本检查、保存待审稿、人工采用；技能不写库或送稿。
模型使用启动时冻结的body路由；失败不绕道重发。
