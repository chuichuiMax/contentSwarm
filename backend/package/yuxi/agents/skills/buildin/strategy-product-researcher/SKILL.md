---
name: strategy-product-researcher
description: 在标题与正文公式锁定后，按公式槽位定向检索公司产品资料并生成可冻结的证据映射。
version: 1.1.0
---

# 策略驱动的产品资料取材

1. 只处理当前节点 `payload`，不得修改 `payload.strategy_snapshot` 中的内容方向、创作手法、标题公式或正文公式。
2. 逐项读取 `payload.product_material_requirements.requirements`，先检查当前 `payload.evidence_bundle`，只有缺失时才调用业务事实或知识库工具。
3. 检索查询必须同时包含内容方向、锁定创作手法、公式编码、资料类型和变量编码，禁止泛化搜索无关资料。
4. 在检索前按正文公式 `structure_schema` 建立段落槽位，只把能直接支撑当前业务主题并自然写入具体段落的结果作为证据。通用报价、相邻 SKU 或其他项目资料若不能与当前内容口径一致，只记录未解决说明，不得创建正文证据。
5. 允许用于标题或正文的新知识库证据，其 `metadata` 必须包含 `writing_ready=true`、对应公式编码、正文段落 `formula_section`、`integration_instruction` 和 `relevance_reason`；正文段落必须来自锁定公式结构。
6. 产品介绍必须来自公司正式产品或服务文档；保存适用人群、卖点、使用边界和来源版本。
7. 价格、报价、优惠、效果承诺等资料必须标记 `risk_level=high_risk`。价格资料的 `metadata` 必须包含 `material_type=price`、适用范围和价格口径，等待人工确认后才能冻结。
8. 案例证明不得把不同客户或项目的数字拼接成一个案例；客户身份、地域或敏感信息只能在来源明确允许时使用。
9. 爆款样例只能标记 `allowed_usage=[style_reference]`，`metadata.material_type=viral_example` 且 `metadata.usage_mode=structure_reference_only`。只提取结构、节奏和表达模式，禁止复制原句，也不得把其中事实或数字映射到标题、正文槽位。
10. `slot_mappings.slot` 必须来自资料需求中的 `requirement_id`，`target_usage` 必须来自该需求自己的 `target_usages`，新证据的 `metadata.material_type` 必须等于该需求的 `material_type`。案例证明只能映射 `case_proof`，绝不能映射 `viral_example`；只有真实爆款样例才能进入 `style_reference`。
11. 知识库证据的 `source_id` 必须原样使用 `query_kb` 返回的结果 `id`；不得使用文件名、知识库名或自行生成的值代替。知识库、文档和分块元数据由系统按该结果 ID 校验并冻结。
12. 可选资料没有合法证据时不要创建 `slot_mapping`，不得拿其他资料类型补位。必需资料缺失时写入 `unresolved_questions`，不得猜测、补造或用爆款样例替代公司业务事实。
13. 不生成标题、正文、大纲或视觉文案。
14. 严格提交 `ProductEvidenceCollectionResultV1`：`evidence_items`、`citations`、`slot_mappings`、`unresolved_questions`；提交工具返回校验错误时，按错误修正映射后重新提交。
