---
name: viral-cover-matcher
description: 根据文章主题生成视觉意图，并从真实素材中选择与装修内容相符的首图。
version: 1.1.0
---

# 首图内容匹配

服务端已经根据标题、正文和锁定公式计算 `payload.required_visual_intent`。将它逐字复制到 `visual_intent`，不得再次推导或改成其他枚举值。素材 ID 逐字复制 `payload.required_source_asset_ids`；只能引用 `payload.allowed_visual_evidence_ids` 中列出的 visual Evidence，不得凭文件名猜图。

- whole_house_quote：整体房屋装修报价清单，优先装修效果图或同空间改造前后对比图。
- partial_renovation：局部改造，优先对应空间的现场或前后对比。
- craft_detail：工艺或施工知识，优先对应节点、材料或细节近景。
- case_result：案例结果，优先有证据的完工效果或前后对比。

所选图必须与主卖点一致，在 selection_reason 说明素材信号与内容的对应关系。没有匹配素材时在 risks 明确记录缺口，禁止用无关图片填充。封面文字只使用有证据事实。
