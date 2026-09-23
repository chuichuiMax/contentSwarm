---
name: content-fact-extractor
description: 从用户本次输入原文中摘录创作计划缺少的业务变量，不做策略判断、不查询外部资料。
---

# 创作事实抽取

只处理 `creation_plan_gap_analysis.missing_variable_codes` 与 `candidate_variable_codes` 中列出的变量。候选变量用于工艺/日常组合的选式，有原文才摘录；不存在时列入 unresolved，不表示生产必须补齐所有候选。公式与订单由程序在抽取后选定并冻结。

1. 按 `creation_plan_gap_analysis.missing_variable_definitions` 与 `candidate_variable_definitions` 中的中文名称和类型理解变量，再逐项在 `content_brief.user_request`、`form_values.user_request` 或其他已提供简报字段中寻找明确原文。`product` 表示“产品或服务”，可逐字摘录用户明确提供的装修业务、工种或服务项目，例如“装修行业”“水电”“泥瓦”；不得因为代码名是 product 而把已明确提供的服务判为无法确认。
2. 每条事实填写一个变量编码、`value` 和 `source_quote`。`source_quote` 必须是用户输入中连续、逐字存在的短片段；`value` 必须逐字等于 `source_quote`，不得归纳、换算、拆分金额或补齐单位。
3. 一段原文可以同时证明多个变量，但每个变量单独提交。`value_type=list` 的变量可按不同 `source_quote` 提交多条事实；其他变量只能提交一条。不得重复提交同一变量的相同原文。无法从原文直接证明的变量放入 `unresolved_variable_codes`。
4. 不选择创作类型、标题公式、正文公式、创作手法或爆款参考；不评价候选，不生成标题或正文。
5. 不把爆款、知识库或常识当作当前项目事实，不推断成交、结算、包含范围或效果。`craft_count` 仅为本次工序数量，`craft_duration` 仅为本次实际施工耗时，不得取年龄、工龄、服务工地数、序列号或图片地址中的数字。`result` 仅取本次已发生的结果/进展；期望、承诺、通用口碑都不是本次结果。`inspection` / `kickoff` 仅在本次明确出现巡检/开工事项时摘录。`case_background` 可为本次工地或现场背景；标签只证明主题，不能据此扩写未提供的施工步骤和验收结论。
6. 只调用一次 `submit_content_node_result`，提交 `ExtractedCreationFactsResultV1`。
