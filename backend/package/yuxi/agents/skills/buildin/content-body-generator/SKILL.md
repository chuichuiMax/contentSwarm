---
name: content-body-generator
description: 使用人工锁定标题和同一份 ContentBrief、StrategyPlan、EvidenceBundle 生成正文与话题。仅在 Yuxi 内容工作流的正文生成或定向重写节点使用。
---

# 正文与话题生成

1. 只读取当前节点 `payload`；在简化工作流中与标题、大纲一次生成并保持一致。
   - `payload.runtime_config_snapshot.creation_mode=original` 时，按锁定公式原创，不使用爆款结构参考。
   - `payload.runtime_config_snapshot.creation_mode=viral_rewrite` 时，必须使用冻结 EvidenceBundle 中唯一 `selected_reference=true` 的爆款结构蓝图组织标题、大纲和正文；只能仿写抽象结构、节奏、钩子和互动方式，业务事实仍只来自允许用于标题或正文的 Evidence。
2. 首次 `generate_content` 在同一次输出中生成大纲，并严格沿用该大纲；已有 `payload.content_outline` 且审核未要求调整结构时沿用已有大纲。按 `payload.strategy_snapshot.body_formula` 逐段兑现结构，并让 `creation_methods` 贯穿全文。
   - `payload.formula_lexicon_bundle.required=true` 时，必须读取 `formula_lexicon_bundle.body` 中全部指定词库，并按各段 `lexicon_calls` 只使用对应词库的表达词条；不得跳过、改用标题词库或凭记忆补词。
   - 当正文公式包含 `body_calling` 时，逐段执行其 `instruction` 和 `fill_rule`，只使用该段声明的 `lexicon_calls`；词库只决定表达，不得提供事实。
   - 若大纲选择了 `variant_key`，正文的反差段只能使用该维度，禁止混入其他反差逻辑。
   - 把正文实际采用的词库编码和原样词条写入 `draft.lexicon_usage`；必须覆盖各固定段落词库和所选 `variant_key` 对应词库，未实际使用的词条不得虚报。
3. 按锁定公式需要，从 `payload.evidence_bundle` 植入产品卖点、适用人群、价格、品牌或案例证明，只使用允许用于正文的证据。引用知识库价格证据时，必须选择与当前内容相关的具体 SKU，并写出该 SKU 的明确价格和单位；不得只写“按元/平方米、元/项或元/间计价”“可参考价格表”“以实际为准”等空泛口径。若证据说明该价格不等同于本案总预算，应同时保留适用范围说明。
   - 标准单价可独立组成报价明细或工价清单，明确标注“标准单价参考”并保留项目、单位、范围。项目总预算可以另行展示，两者无需对应或加总一致；没有工程量、小计或某些类别的报价不影响展示已有单价。不得为了凑总预算倒推单价、工程量或小计，也不得称为本案实际成交/结算明细。适用范围说明写在价格清单附近，不要用索要实际报价的提示替代正文中的具体单价。
4. `style_reference` 仅用于开头钩子、结构、节奏、互动方式和 emoji/表情符号位置模式参考；可沿用抽象模式，禁止复制原句、事实或数字，禁止写入 `paragraph_evidence`。
5. 只使用 `payload.evidence_bundle` 中允许用于正文的事实；不得调用业务事实或知识库工具，不得补造客户、价格、参数、统计或效果。
6. 正文和话题中的事实数字必须来自运行时提供的数字白名单，不得编造数字示例、对比数字或统计数字。只有冻结爆款蓝图的 `list_pattern.type=numbered` 或包含编号的 `mixed` 时，允许使用与参考一致的行首顺序编号；这些编号只承担结构导航，不得写进事实句或 Evidence。其他结构不得擅自添加阿拉伯数字或数字 Emoji 编号。
7. 输出前逐项核对正文和话题里的数字；发现白名单外数字时，删除数字或把句子改为不含数字的事实表达。
8. 正文控制在 200～650 字；每个事实段落通过 `paragraph_evidence` 关联实际使用的 Evidence ID。关联价格知识证据的段落必须真实出现该证据中的至少一个具体价格值，不能只挂 Evidence ID。
   - 公式规定的是语义顺序，不要求把每个结构段写成单一长段。在 `generate_content` 节点中，原创正文内已有多个并列改造、材料或验收事项时，必须按已激活的 `viral-layout-formatter` 拆成逐项短行，并按 `content-human-expression` 给独立事项落实语义 Emoji；一组短行仍属于原结构段，证据引用保持对应，不能为了少换行或“平台无关”把条目重新合并。仿写继续保留冻结蓝图的列表类型。
9. 简化工作流严格提交 `GeneratedContentResultV1`，正文放入 `draft`；旧工作流仍提交 `ContentDraftResultV1`。

## 装修工长新版公式

- 当策略快照包含 `direction_blueprint` 时，以 `layer_sequence` 的顺序逐层写作，并逐项执行 `phrase_composition`：`fixed` 只能使用 `allowed_groups`，`all` 使用本次可用的全部对应组，`random` 必须在允许范围内选取 `min_groups` 至 `max_groups` 组。词组规定表达类别，具体项目事实仍只能来自 EvidenceBundle。
- 七个一级方向不得跨行混用：自我介绍只写自我介绍；项目单价只写人工单价且不得新增证据层；单价+面积、工种总价、人工+辅材的内容组固定为施工报价、证据组固定为真实报价；工艺展示和日常工作的证据只可采用工地照片或工艺节点。若可用资料达不到某层最小组数，报告资料缺口，不得跳层、减量或编造。
- 当正文公式为 FRB01～FRB09 时，逐段执行锁定结构，不把九种模式压缩成通用报价、案例、干货或人设模板。工长人设只改变说法，不改变事实；每篇最多使用二至三项与当前场景直接相关的优势。
- 报价必须说明是标准单价、项目报价、预算还是结算，并保留地区、项目、单位和包含范围。FRB07 的乘算结果、FRB08/FRB09 的分项合计只能使用程序已校验的同源数据；输入没有校验结果时不得自行计算或补齐。
- 工艺图片只描述可见空间、施工阶段、工种和细节；不得从图片推断价格、工期、材料品牌、质保、工人数、客户评价或完工效果。
- 标题中的唯一主卖点须在正文首屏或前两段兑现；结尾使用与本次内容相符的具体咨询、核价、看工地或沟通入口，不生成虚假的限时、免费或保证承诺。

事实口径保持一致，呈现方式遵守当前 `channel_profile`：允许 Emoji 的社交渠道落实情绪与数据、事项导航，明确禁用时不用；不得用“平台无关”要求抹去渠道排版与人设表达。话题提供 3～8 个，不执行发布。
