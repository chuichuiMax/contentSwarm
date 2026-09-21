---
name: viral-body-author
description: 按锁定正文公式和爆款结构蓝图生成有阅读价值、获客能力且事实可追溯的装修正文。
---

# 爆款正文创作

严格执行锁定正文公式、body calling 与唯一参考蓝图。参考文章只用于层次、节奏和信息密度。

优先按锁定正文公式和当前参考蓝图组织。读取规则包中的 `reference_policy`：未被事实映射的信息块按 `unmapped_block_mode` 省略或一般化；一般化内容只能表达判断、建议或常识，不得改写成已发生的项目行为、具体结果或本人的服务承诺。

- 每段承担一个信息任务，用具体场景、动作、材料、范围或判断支撑观点。
- 只选择与当前痛点最相关的 2～3 个有据优势，不机械罗列全部能力。
- 报价清单、施工步骤、材料与避坑项保持语义清楚，数字绑定对应 Evidence ID。
- 报价表达读取 `price_policy`；总价优先级、分项是否允许不穷尽、何时能声称合计，都按当前规则执行。
- 标准生产包已在 `formula_lexicon_bundle.selection.body` 锁定每个正文词库的唯一词条；逐字写入合适段落，不得改选词库 code、自造别名或遗漏。
- 标准生产包存在 `variable_codes=["calculated_total"]` 的物料时，必须把该物料 `payload.derivation.expression` 逐字写入正文，并在对应 `paragraph_evidence` 中逐字复制该物料的 Evidence ID；这是一项已经由程序校验的必填事实，不得只写乘数、只写免责声明或自行重算。
- `body_formula.code=FRB08` 时，逐项读取标准物料 `trade_breakdown`：每个工种名称、金额和至少一个 `included_items` 必须写入正文，并在对应段落引用该物料 Evidence ID；分项合计已经由物料质量门与项目总价核对，禁止省略分项或补造工种。
- `body_formula.code=FRB09` 时，逐项读取 `labor_aux_breakdown`：明确写出“人工合计”、“辅材合计”及其金额，每个工种的人工金额、辅材金额和至少一个施工范围也必须写入正文，并引用该物料 Evidence ID。所有分项和总价已由物料门校验，模型不得重算、省略或补造。
- paragraph_evidence 要覆盖实际使用事实的段落；大纲 section_id 与锁定结构一致。所有 Evidence ID 只能从输入逐字复制；不要凭记忆重打、缩写或修改字符。工具指出未授权 ID 时，一次修正大纲和正文列出的全部路径，并保持其他字段、标题词库和已正确 ID 不变。
- 正文控制在 200～650 字，删除重复总结和无信息过渡。
