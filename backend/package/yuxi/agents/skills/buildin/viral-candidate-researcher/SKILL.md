---
name: viral-candidate-researcher
description: 根据当前任务变量从爆款库检索多个候选，保留供后续比较的原始结构信息，但不选择最终参考。
---

# 爆款候选检索

- 只在 `runtime_config_snapshot.creation_mode=viral_rewrite` 时执行；原创模式由工作流直接跳过本节点。
- 从所有非空输入建立检索画像，至少覆盖行业、渠道、内容目标、受众、产品或项目、场景、核心痛点、面积、预算、工期和已有结果。
- 必须先且只查询一次已授权的“爆款库”，不得在查询前提交，不得读取全文或继续检索。检索词应优先使用最能区分当前任务的项目、场景、痛点和人群，不能只用行业或公式编码宽泛检索。
- 从本次返回结果中保留匹配度最高的 2 个具有实际可比性的候选，不得只返回第一条，也不得在本节点决定最终参考。
- 每个候选的 `value` 只保存当前检索片段中足以识别标题、开头、信息块顺序、列表、节奏、Emoji 和互动方式的连续原文，单篇不超过 800 个字符；不要在候选阶段复述、分析或扩写原文。
- 候选 Evidence 必须使用新的 ID、实际检索结果 `source_id`、`allowed_usage=[style_reference]`、`metadata.material_type=viral_example`、`metadata.usage_mode=structure_reference_only`，且不得设置 `selected_reference=true`。
- 爆款中的品牌、价格、数字、客户和结果只属于候选原文，不得转成业务 Evidence。
- 没有候选时明确写入 `unresolved_questions`，不得虚构样例。
- 查询结束后立即严格提交一次 `ViralCandidateCollectionResultV1`，不生成标题、正文或最终结构蓝图。
