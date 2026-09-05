---
name: content-body-generator
description: 使用人工锁定标题和同一份 ContentBrief、StrategyPlan、EvidenceBundle 生成正文与话题。仅在 Yuxi 内容工作流的正文生成或定向重写节点使用。
---

# 正文与话题生成

1. 只读取当前节点 `payload`；在简化工作流中与标题、大纲一次生成并保持一致。
   - `payload.runtime_config_snapshot.creation_mode=original` 时，按锁定公式原创，不使用爆款结构参考。
   - `payload.runtime_config_snapshot.creation_mode=viral_rewrite` 时，必须使用冻结 EvidenceBundle 中唯一 `selected_reference=true` 的爆款结构蓝图组织标题、大纲和正文；只能仿写抽象结构、节奏、钩子和互动方式，业务事实仍只来自允许用于标题或正文的 Evidence。
2. 严格沿用 `payload.content_outline`，按 `payload.strategy_snapshot.body_formula` 逐段兑现结构，并让 `creation_methods` 贯穿全文。
   - `payload.formula_lexicon_bundle.required=true` 时，必须读取 `formula_lexicon_bundle.body` 中全部指定词库，并按各段 `lexicon_calls` 只使用对应词库的表达词条；不得跳过、改用标题词库或凭记忆补词。
   - 当正文公式包含 `body_calling` 时，逐段执行其 `instruction` 和 `fill_rule`，只使用该段声明的 `lexicon_calls`；词库只决定表达，不得提供事实。
   - 若大纲选择了 `variant_key`，正文的反差段只能使用该维度，禁止混入其他反差逻辑。
   - 把正文实际采用的词库编码和原样词条写入 `draft.lexicon_usage`；必须覆盖各固定段落词库和所选 `variant_key` 对应词库，未实际使用的词条不得虚报。
3. 按锁定公式需要，从 `payload.evidence_bundle` 植入产品卖点、适用人群、价格、品牌或案例证明，只使用允许用于正文的证据。引用知识库价格证据时，必须选择与当前内容相关的具体 SKU，并写出该 SKU 的明确价格和单位；不得只写“按元/平方米、元/项或元/间计价”“可参考价格表”“以实际为准”等空泛口径。若证据说明该价格不等同于本案总预算，应同时保留适用范围说明。
4. `style_reference` 仅用于开头钩子、结构、节奏、互动方式和 emoji/表情符号位置模式参考；可沿用抽象模式，禁止复制原句、事实或数字，禁止写入 `paragraph_evidence`。
5. 只使用 `payload.evidence_bundle` 中允许用于正文的事实；不得调用业务事实或知识库工具，不得补造客户、价格、参数、统计或效果。
6. 正文和话题中的事实数字必须来自运行时提供的数字白名单，不得编造数字示例、对比数字或统计数字。只有冻结爆款蓝图的 `list_pattern.type=numbered` 或包含编号的 `mixed` 时，允许使用与参考一致的行首顺序编号；这些编号只承担结构导航，不得写进事实句或 Evidence。其他结构不得擅自添加阿拉伯数字或数字 Emoji 编号。
7. 输出前逐项核对正文和话题里的数字；发现白名单外数字时，删除数字或把句子改为不含数字的事实表达。
8. 正文控制在 200～650 字；每个事实段落通过 `paragraph_evidence` 关联实际使用的 Evidence ID。关联价格知识证据的段落必须真实出现该证据中的至少一个具体价格值，不能只挂 Evidence ID。
9. 简化工作流严格提交 `GeneratedContentResultV1`，正文放入 `draft`；旧工作流仍提交 `ContentDraftResultV1`。

保持平台无关正文；话题提供 3～8 个，不执行发布。
