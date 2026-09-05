---
name: content-outline-builder
description: 把已锁定的正文 Pattern、SlotPlan 和 EvidenceBundle 编译为段落大纲与证据使用计划，不生成新的业务事实。
version: 2.0.0
---

# 内容大纲编译

只读取当前节点 `payload`，严格按照 `payload.strategy_snapshot.body_formula` 的原版正文结构，为同次生成的最终标题组织大纲。

- 每个段落必须保存段落目的、允许使用的 Slot 和 Evidence ID。
- 当 `body_formula.body_calling` 存在时，必须把它当作正文执行契约：`sections` 严格按其中的 `id` 和顺序输出，不得增删、合并或换序；每段同时遵守 `instruction`、`fill_rule`、`lexicon_calls` 和 `fact_source`。
- 当 `body_calling.variants` 非空时，只选择其中一个 `id` 写入 `variant_key`，全文只使用该维度，禁止混用其他维度；没有 variants 时不要提交 `variant_key`。
- `lexicon_calls` 只提供表达方向，不能充当事实、数字、案例、身份或承诺来源。
- 从 `payload.evidence_bundle` 把产品介绍、案例证明、价格和品牌资料分配到对应段落，只使用允许用于正文的证据。
- 爆款样例只能影响结构节奏，不能成为段落事实或 Evidence ID。
- 事实性段落只能使用 EvidenceBundle 中已有来源。
- 缺少必填事实时标记阻断或请求补充，不得自行构造示例数据。
- 主叙事轴贯穿所有段落，不能引入第二条竞争叙事。
- 不选择下一个节点，不修改工作流结构。
- 在简化工作流中把 `OutlineResultV1` 放入 `GeneratedContentResultV1.outline`，不单独提交节点结果。
