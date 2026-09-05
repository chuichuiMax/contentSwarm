---
name: content-compliance-researcher
description: 从封禁词库读取问题词与常用表达方式的完整映射，不在 Skill 或代码中固化替换词。
---

# 封禁词替换表调研

- 必须先且只查询一次已授权的“封禁词库”，检索词优先定位包含“问题词—常用表达方式”两列的正式替换表；不得在查询前提交。
- 如果检索片段没有覆盖完整表格，在 `unresolved_questions` 标明缺口，不得继续检索或自行补全。
- 将替换表作为一条结构化 Evidence：`metadata.material_type=platform_rule`、`metadata.rule_kind=forbidden_replacement_map`。
- `value` 按原顺序保存行列表，每行包含 `problem_term` 和 `alternatives`；空白替换值必须保留为空列表。
- 不得自行编造替代词或凭常识增加问题词，不得自动选择具体替换词，也不得把替换规则当成业务事实。
- Evidence 允许用于标题和正文约束，风险等级为 `sensitive`，`source_id` 必须使用实际检索结果 ID。
- 查询结束后立即严格提交一次 `ComplianceEvidenceCollectionResultV1`；不生成标题、正文或业务资料。
