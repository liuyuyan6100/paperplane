---
name: extract-materials
version: 1.1.0
description: 所选素材整理
category: research
enabled: true
---

# 所选素材整理

输入：materials（最多六份已选择且已读取的素材快照），可选scope=selected。
返回：materials深拷贝、source_types、total_found、scope。保留原始顺序、来源、版本及正文。
这是确定性的素材准备步骤，不调用模型、不读取磁盘、不自动联网或重新查询知识库。
查库、阅读、导入仍由现有资料库与对话工具完成；生成任务只用冻结快照。
旧Phase2草案中的web/all自动搜索不符合已有取材边界，未作为本次实现功能。
