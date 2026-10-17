---
name: review-performance
version: 1.0.0
description: 基于已保存发表数据快照提出有依据的复盘候选
category: content
enabled: true
---

# 发表数据复盘

接收post、profile、model_route及instruction；路由task沿既有配置使用review。
PerformanceManager提供已冻结的发表内容、指标、证据与画像，保留未取得和零值区别及长文截断提示。
返回raw，由既有parse_result验证证据键和候选结构，再按run_token保存结果。
不自行拉取指标、修改发布记录或保存画像经验；经验仍经既有候选及人工确认步骤。
失败沿用现有失败状态与手动重试，不经旧实现重发。
