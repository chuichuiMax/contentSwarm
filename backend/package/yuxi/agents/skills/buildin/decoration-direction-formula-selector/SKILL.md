---
name: decoration-direction-formula-selector
description: 装修创作使用已选或基于资料与参考自动采用的内容方向，在方向内匹配标题和正文公式，不对公式数值评分。
---

# 装修方向内选式

读取运行时提供的 `strategy_candidates`。只在 `strategy_mode=direction_scoped` 时使用本 Skill。

1. auto_direction=true 时，从 direction_options 中选出与真实资料、选中参考结构相符的方向，写入 direction_code，并说明原因；只比较该方向 title_formula_codes、body_formula_codes 内公式，使用其 valid_formula_pairs。不必等待用户选择方向。auto_direction 未启用时，保持 strategy_candidates.direction_code 的锁定方向，不改动历史手动模式。
2. 比较方向内的标题和正文公式适用说明，核对本次输入能否支持必要事实。总预算不能代替分项报价，计划结果不能代替已发生结果。
3. 从 `valid_formula_pairs` 中选择一个资料可支持且符合表达目标的配对。唯一合格配对可直接采用；多个配对根据输入语义匹配，不根据候选位置。
4. 每个被比较公式给出简短理由和非空输入字段路径，或明确淘汰理由；公式 `dimensions` 留空、`total` 为 null，不能制造数值评分。
5. 确定方向后不跨方向、借用已停用公式或为了找到公式而修改用户输入。没有合格配对时返回 `needs_input` 或 `no_candidate` 及具体原因。
6. 创作手法仍按运行时提供的 `scoring.method` 独立评分。标题和正文声明的 compatible_methods 取交集，主手法与辅助项都必须在交集中。主手法按加权总分降序、scoring.method.tie_break 各维度依次降序、candidate_id 升序选择最高项；全维度同分时 M01 优先于 M03，不以语义偏好覆盖同分规则。组合组成员只是来源信息，不要求全部采用；S01 不在交集中时不可选。

本 Skill 不检索原文、不抽取蓝图、不生成标题或正文。
