---
name: viral-reference-selector
description: 比较爆款候选与当前任务的真实输入变量，选择唯一可填充参考并动态抽取结构蓝图。
---

# 爆款参考匹配与结构解析

1. 只读取输入中的 `viral_candidate_collection`、当前 `content_brief` 和锁定策略；本节点不得再次查询知识库。业务规则、价格与合规证据由后续汇合节点交给正文创作，不在这里重复处理。
2. 原创模式由工作流直接跳过。爆款仿写模式必须先从全部非空输入建立匹配画像，记录实际采用的字段路径，不得只使用行业或公式做宽泛匹配。
3. 先执行硬性淘汰。行业、渠道、内容目标、核心受众或场景明显不兼容的候选不得入选；依赖当前 `content_brief` 无法填充的关键事实槽位也不得入选。
4. 候选依赖分项报价而当前只有总预算，依赖完工或结算结果而当前只有方案、预计工期或目标结果时，必须淘汰。知识库中的相邻服务 SKU 或其他项目案例不能补足当前项目证据。
5. 对通过硬性检查的候选逐篇比较输入变量语义、受众场景、内容目标、锁定公式和结构可填充性；不得直接选择候选列表第一项。
6. 没有可完整填充的候选时提交未解决问题，不得随机选择、退化为原创或虚构参考。
7. 选中候选后提交：
   - `selected_candidate_id`：必须来自输入候选；
   - `selection_reason`：说明与当前输入的匹配点；
   - `selection_basis.input_variable_paths`：参与选择的非空字段路径；
   - `selection_basis.matched_dimensions`：项目、场景、痛点、受众、目标、渠道和公式匹配；
   - `selection_basis.structure_fillability`：结构所需资料及对应的 `content_brief` 字段路径，且 `unfilled_required_slots=[]`；
   - `selection_basis.candidate_comparison`：每篇候选的通过或淘汰结论与原因。
8. `reference_blueprint` 必须从选中候选真实内容动态抽取：
   - `title_pattern` 与 `title_slot_sequence`；
   - `opening_hook`；
   - `content_block_sequence`；
   - `narrative_structure`；
   - `paragraph_rhythm`；
   - `list_pattern.type=none|numbered|emoji|bulleted|mixed`、位置与可观察数量；
	   - `emoji_pattern`：除数量和用途外，必须记录每个可观察 Emoji 所在的结构块、`relative_position=start|middle|end`、相邻语义锨点及功能；不得只写“少量点缀”这类无法执行的摘要；
   - `interaction_style`；
   - 可选的 `emotion_curve`。
9. 不得先套用“1–4 编号、固定五段、固定 Emoji 数量”等预设模板。候选没有编号时不得标记为编号结构。
10. 蓝图只能保留抽象结构、节奏和互动方式，不得复制候选原句、价格、品牌、客户、事实、数字或承诺。
11. 严格提交 `ViralReferenceSelectionResultV1`；不生成标题、正文或话题。
