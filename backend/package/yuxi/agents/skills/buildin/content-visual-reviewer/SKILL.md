---
name: content-visual-reviewer
description: 按锁定视觉方案和渠道规范审核封面资产，不修改原资产。
version: 1.1.0
---

# 视觉资产审核

- 只读取当前节点 `payload`，对 `payload.cover_assets` 逐项检查尺寸、安全区、文字、素材来源、品牌一致性和合规风险。
- 严格提交 `VisualReviewResultV1`：每个资产只返回 `asset_id`、`status`、`issues`，总状态只能为 passed、warning 或 blocked。
- `recommended_asset_id` 必须来自本次 `payload.cover_assets`，没有可推荐资产时返回 null。
- 不直接编辑、重生成或删除资产。
