---
name: content-business-rule-researcher
description: 按已锁定内容策略检索品牌业务事实与平台业务规则，不处理价格、封禁词或爆款样例。
---

# 业务与规则调研

- 以 `payload.content_brief`、`payload.strategy_snapshot` 和现有 `payload.evidence_bundle` 为准，先确认锁定公式尚缺哪些可写入事实。
- 必须先分别查询一次已授权的“品牌知识库”和“平台规则”，两次查询都完成后才能提交结果；即使预计结果为空，也不得在查询前提交。
- 不得查询价格库、封禁词库或爆款库，不得重复查询或读取全文。
- 品牌资料用于核验产品、服务、卖点、工艺、流程、材料和真实案例；平台规则用于核验行业口径和业务边界，不得当作某个项目已经发生的事实。
- 只有与当前输入主题直接相关、且能自然填入锁定公式具体段落的知识库事实才能进入 `evidence_items`。
- 可用于标题或正文的知识库事实必须设置 `metadata.writing_ready=true`、`metadata.integration_instruction` 和 `metadata.relevance_reason`。
- 用于标题时，`metadata.title_formula_code` 必须逐字复制 `payload.strategy_snapshot.title_formula.code`；用于正文时，`metadata.body_formula_code` 必须逐字复制 `payload.strategy_snapshot.body_formula.code`，`metadata.formula_section` 必须逐字选自 `payload.strategy_snapshot.body_formula.structure_schema`。禁止填写公式名称、版本 ID 或自行改写段落名。
- 不得用其他客户案例覆盖当前任务变量，不得把通用规则改写成当前项目结果。
- 新证据必须使用检索结果原始 `source_id`，并使用新的 Evidence ID；现有 Evidence 只能读取，不能重复提交。
- 没有合适事实时提交空列表，并在 `unresolved_questions` 说明原因，不得为了引用而硬塞资料。
- 两次检索结束后立即严格提交一次 `BusinessRuleEvidenceCollectionResultV1`，不生成标题、正文或爆款结构。
