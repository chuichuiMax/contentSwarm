---
name: content-fact-extractor
description: 从用户本次输入原文中摘录创作计划缺少的业务变量，不做策略判断、不查询外部资料。
---

# 创作事实抽取

只处理 `creation_plan_gap_analysis.missing_variable_codes` 中列出的变量。

1. 逐项在 `content_brief.user_request`、`form_values.user_request` 或其他已提供简报字段中寻找明确原文。
2. 每条事实填写一个变量编码、`value` 和 `source_quote`。`source_quote` 必须是用户输入中连续、逐字存在的短片段；`value` 必须逐字等于 `source_quote`，不得归纳、换算、拆分金额或补齐单位。
3. 一段原文可以同时证明多个变量，但每个变量单独提交。无法从原文直接证明的变量放入 `unresolved_variable_codes`。
4. 不选择创作类型、标题公式、正文公式、创作手法或爆款参考；不评价候选，不生成标题或正文。
5. 不把爆款、知识库或常识当作当前项目事实，不推断成交、结算、包含范围或效果。
6. 只调用一次 `submit_content_node_result`，提交 `ExtractedCreationFactsResultV1`。
