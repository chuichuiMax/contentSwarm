---
name: viral-title-author
description: 按真实事实和参考标题的吸引方式创作完整、自然、由正文兑现的装修标题。
---

# 爆款标题创作

只负责标题；公式代码和 Evidence ID 沿用锁定值。

- 读取 `strategy_snapshot.title_formula.source_content.slot_schema`（标准流程位于 `production_pack`）。每个对象是必填槽位；同槽变量与词库只选一个有据表达，旧公式无槽位时才按 `variable_schema`。
- 先确定正文真正要告诉读者的判断或价值，再学习唯一参考 `reference_blueprint.title_pattern/title_slot_sequence` 的吸引方式；用本篇事实写出完整关系。公式约束信息覆盖，不要求把槽位名词依次拼接。`reference_examples` 只提供节奏，不提供本篇事实。
- `persona_fact` 槽位可用已核验身份或年限表达；无该槽位不强塞身份。`craft_role` 中的作者身份可作说话人，如“工长聊拆除”；人设的水电、泥瓦技能不证明本篇施工对象是水电或泥瓦。
- `formula_lexicon_bundle.selection.title` 是可选的冻结词条，非全部必写。按语义选用，禁止按字数或排序固定选第一项。情绪必须有对象和正文理由：“劝退、后悔、避雷”不能无缘无故贴在工艺后；没有负面依据就用贴合正文的中性提醒等候选。
- 反例“水电拆除，劝退”：工种与施工对象被混淆，且未交代劝退什么。应从正文提取具体判断或建议，写清对象与动作，不用三个词代替完整含义。
- 标题只突出一个主卖点，写明具体判断、建议或问题，前两段兑现其含义。“聊拆除，听劝”只报话题和情绪，没有说明听什么劝；必须写出建议的动作/对象，或明确要解决的问题，不能把核心信息全部藏进正文。先满足事实槽位，再删重复或公式外元素，按 `channel_profile.title_constraints` 控制字数，不为变短牺牲语义。
- 价格只用本次已确认口径，不把单价当成交价、业主原报价当工长报价，不补数字。
- `title.lexicon_usage` 只登记标题中实际出现的冻结词条，候选必须逐字使用；系统回填元数据，不得虚报未用词。
- 标题回修按阻断意见重写完整含义，保留无关正文和话题；不得只换情绪词继续拼接。`repair_constraints` 已锁定标题时原样返回。
- 逐项读取 `strategy_snapshot.title_formula.source_content.slot_schema`（标准流程位于 `production_pack`）。每个对象是必填槽位；同一对象内的变量与词库只选一个有据表达，“面积/房型”等斜杠不是两项都写。旧公式无槽位时才按 `variable_schema` 填充。
- 读取 `strategy_snapshot.title_formula.reference_examples` 学习槽位顺序、连接方式、节奏和标点，然后全部替换为本次冻结事实；示例不提供城市、面积、户型、价格、结果或身份事实，禁止照抄示例内容。
- `persona_fact` 槽位可用已核验身份或年限表达；公式没有该槽位时，不得因正文人设物料而强塞身份。
- 必填槽位全部覆盖后，才从地域词、业务词、房屋词、需求词、风格词、人群词、价格词、结果词、身份词中取舍公式外的可选元素；组成一条自然中文短句，元素拥挤时先删除公式外元素，再在 one-of 槽位内换用更短的等价来源。
- 提交前按 `production_pack.channel_profile.title_constraints` 逐字计数，标题必须满足渠道最大长度；相同语义的产品事实与必选词库词应合并表达，不得重复堆叠后交给审核处理。
- 标题只突出一个主卖点，避免关键词并排堆砌、解释策略或复制参考标题。
- `formula_lexicon_bundle.selection.title` 中，仅词库来源的必填槽位（如 FRT16 情绪）必须逐字写入已选词条；同一词库只写一个。带业务变量的 one-of 槽位可以不写词库，改用已核验事实。采用的词条必须逐字使用，禁止近义改写。
- 报价场景且证据同时提供城市、面积、业主报价和工长结果价时，优先使用自然化的“城市＋面积＋业主报价多少＋我们多少搞定”句式；缺任一事实就删掉该槽位，禁止补数字。
- 标题中的价格角色要清楚，不能把标准单价写成项目成交价，也不能把业主原报价写成工长报价。
- 回修标题时正文与话题原样返回；命中 `TITLE_ALIGNMENT`、`TITLE_FORMULA_MISMATCH` 或 `TITLE_REQUIRED_FACT_MISSING` 时按 `slot_schema` 和 `generation_slots` 中的可接受原文逐槽复核，新标题必须写入缺失槽位的冻结词（工艺可用较短可接受项，情绪必须用已选词条），且与上一轮不同，one-of 槽位仍只选择一个来源。
- `title.lexicon_usage` 只登记标题中实际出现的冻结词条，未采用项不得虚报。`selected_terms` 只能取同 code 的 `lexicon_constraints` 候选并逐字复制；不得自造近义词或使用被事实门排除的词，结果词必须有已批准物料。
