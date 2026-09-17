---
name: content-reviewer
description: 审核 Yuxi 生成内容的创作手法贯穿、公式执行、事实一致性、人设语气、封禁词替换和风险表达。仅在确定性校验完成后的内容审核节点使用。
---

# 内容审核

## 审核范围与表达验收

先读取 `payload.review_scope`：`expression` 是默认普通模式，审核正文表情与首尾人设表达；`emoji` 是历史范围标识，按相同表达规则执行；`full` 执行下文完整审核，也包含表情与人设验收。普通范围核对人设句的事实依据及下述锁定创作类型/组合要求，不重审无关业务事实。所有范围都必须返回下面三项表情检查和三项人设检查，不能用空 checks 代替实际审核：

- `EMOJI_COVERAGE`：按正文已有信息检查情绪/判断、数据类别、独立事项、提醒和互动的适用功能。报价清单的拆除、泥工、水电、吊顶、油漆等独立工种逐项导航；面积和费用是不同数据类别。同类金额可由清晰的费用分组导航，不要求每个数字都贴钱袋。不适用的功能无需补造。
- `EMOJI_APPROPRIATENESS`：符号紧邻实际语义，专业人设使用贴切图标；检查误配、无意义重复、连续堆叠或挡住阅读的问题。工种列表连续行首图标属于有效导航，不能仅因连续就删除；痛点情绪不能由数据图标代替。
- `EMOJI_RESTRICTIONS`：读取 `channel_profile`、`persona_profile` 和 `content_brief` 中用户的明确要求。渠道或用户禁用时，应无 Emoji；少量/克制要求优先，允许清晰同类分组，不强制逐个标记。无明确限制时，参考蓝图“仅一个”“无表情”等只是参考文章的描述，绝不能视为成稿数量上限。

三项分别返回 `passed` 或 `blocked`；违反明确禁用、遗漏适用功能或有干扰阅读的乱配/堆叠时必须阻断，不以 warning 放行。不适用或已满足时返回 passed，并在 message 说明实际检查依据。禁用时前两项按“禁用，无需覆盖”判定，不产生相互矛盾的补表情要求。

阻断项的 `location` 引用具体原文短语或条目；`suggestion` 给出应添加/删除/移动的 Emoji 和落点，不指定全篇数量。只改符号及必要空格，保留原文字、金额、单位、标点、段落顺序、编号、标题和证据引用。上述仅表情建议只改符号；人设阻断允许修正必要文字。普通范围完成表情、人设及适用的组合检查后直接提交，不执行下文完整审核。

通过 `submit_content_node_result` 返回 `status`、`checks`、`evidence_conflicts`。每项检查包含 `code`、`status`、`location`、`message`、`suggestion`、`evidence_ids`，不用 level。无引用时 `evidence_ids=[]`；人设缺失不等于事实冲突；只有确有证据冲突时才填 `evidence_conflicts`，否则为 `[]`。任一项 blocked 则总状态 blocked，否则 passed；不得只写分析文字而不提交结果。

### 首尾人设验收（默认启用）

读取 `persona_profile`、`content_brief.persona`、用户需求中的嵌套人设及允许用于正文的冻结证据。参考文章不是作者人设证据。以下三项必须分别返回 passed 或 blocked，不以 warning 放行：

- `PERSONA_OPENING`：检查正文首个非空自然段（不含标题）是否自然融入与主题相关的已知身份，以及资料支持的经验或擅长领域，并承接读者问题。不要求固定句型或全部人设字段；只有部分资料时按已有信息审核。仅有城市项目背景不能算作者身份，孤立履历堆砌不能算主题关联。
- `PERSONA_CLOSING`：检查正文最后一个非空自然段（不含话题标签）是否用已有服务优势承接本篇，并给出符合真实服务范围和渠道要求的行动邀请。首尾重复自我介绍或空泛“欢迎咨询”需修正；没有优势资料时，已有角色/服务范围的自然承接即可，不要求虚构优势或免费服务。
- `PERSONA_GROUNDING`：核对全文身份、年限、城市服务范围、技能、团队、服务优势、亲历与承诺是否有当前资料依据；不要把案例城市当作服务城市、把擅长某工种扩大为本案亲自施工、把语气偏好写成客户评价。发现无依据的人设事实或首尾矛盾必须阻断，指明应删除或收窄的原句及可用资料依据。

用户明确要求不自我介绍、第三人称、纯清单等形式，渠道/锁定结构不允许，或没有可用人设事实时，对相应首尾项返回 passed 并说明具体不适用原因；用户禁止营销/互动时不要求 CTA，但仍核对已有身份事实。普通模式默认审核，无需用户开启严格审核。

阻断时 `location` 指向实际首段、末段或无依据的具体原句，`suggestion` 说明用哪些已提供的人设事实如何定点修正，不新增履历、优势和承诺；未涉及的正文、标题、金额、单位和结构保留。缺少首尾表达时指出缺失位置，不能建议重写整篇。三项人设与三项表情一起决定总状态，任一 blocked 都必须回修。

### 所选创作类型与表格组合验收（默认执行）

当 strategy_snapshot.direction_blueprint 存在时，普通范围也必须增加以下两项 passed/blocked 检查，不得以“只审核表情人设”跳过。不存在时不要求这两项。

- CREATION_TYPE_ALIGNMENT：核对 content_brief.content_type_code（若有）、strategy_snapshot.content_direction、标题/正文公式及参考类型一致。精确区分项目单价、单价+面积、工种总价、人工+辅材，不能因同属价格营销就混用。以锁定规则快照为依据，不自选其他类型公式。
- COMPOSITION_ALIGNMENT：逐项读取 direction_blueprint.layer_sequence 和 phrase_composition，在实际正文中定位每层、顺序及对应词组组别。检查 selection、min_groups、max_groups、allowed_groups：全取、随机组数、固定组别均按本行执行；“随机取”只能从有事实支持的候选中选，缺少所需材料时阻断并明确缺失，不能虚构来凑组数。项目单价没有独立证据层；施工报价必须使用真实报价，工艺展示/日常工作使用已有工地照片或工艺节点依据。词组组合不是把层名称作为小标题堆在成稿中。

爆款参考只提供同类型的表达和排版参考；与所选类型的层级、组别冲突时，以锁定类型表为准。location 写明实际段落/缺失层，suggestion 给出本行要求及具体修正或缺料说明。上述两项加入总状态，阻断时复用既有正文回修，不能由表情检查通过而放行。无需另建审核节点。

## 完整审核

1. 当前节点 `payload` 必须包含 `content_draft`、`selected_title`、`content_outline`、`strategy_snapshot`、`validation_report` 和 `evidence_bundle`；缺少任一必需输入时直接报告契约错误，不得猜测补齐。
2. 先确认 `validation_report.status` 为 `passed` 或 `warning`。若它为 `blocked`，返回 `REVIEW_CONTRACT_INVALID`，因为确定性阻断不应进入本节点。
3. 对照 `strategy_snapshot` 检查创作手法、标题公式和正文结构，对照 ContentBrief 与 EvidenceBundle 检查事实、人设、语气和来源。
   - 标注为“标准单价参考”的知识库价格可以独立展示，或与项目总预算并列；核对具体价格、单位、适用范围及来源即可。不得仅因没有工程量、分项小计、未覆盖全部类别或不与项目总预算加总一致而阻断、降级或要求补实际报价。若把标准价写成实际成交/结算费用，或虚构工程量、小计来凑总预算，才按事实不一致阻断并指出具体句子。
   - 公式只决定信息顺序，不允许把“旧况、关键数据、过程、结果”等公式步骤写成读者可见的报幕句。
   - 出现“旧况很典型”“关键数据先摊开”“先说背景”“再看过程”“最后看结果”“下面来说”“接下来看看”等元话术，或多个段落使用相同模板句式开场时，必须以 `PERSONA_STYLE_MISMATCH` 阻断并给出直接进入场景或事实的改写建议。
   - 不得因为结构、事实和证据正确，就把明显的提纲填充、审核报告腔或机械连接词判为语气通过。
   - EvidenceBundle 存在 `selected_reference=true` 的爆款结构参考时，必须完整读取其 `reference_blueprint.title_slot_sequence`、`content_block_sequence`、`paragraph_rhythm`、`list_pattern`、`emoji_pattern` 和 `interaction_style`，逐项对照，禁止用审核器自己的通用爆款模板替代冻结蓝图。
   - 逐个检查 `content_block_sequence` 是否在正文中按序可识别，并按 `paragraph_rhythm` 检查真实换行和信息密度；只有蓝图实际要求数据块或列表时才检查这些形式。多个独立信息块被压成一行或结构节点缺失时，以 `CONTENT_STRUCTURE_MISMATCH` 阻断。
   - 列表审核严格服从 `list_pattern`。只有 `type=numbered` 或包含编号的 `mixed` 才要求编号清单，并按蓝图的出现位置和条目节奏检查；`none`、`emoji`、`bulleted` 或不含编号的 `mixed` 不得强制改成 `1–4` 清单。不得设置固定段落数、双换行数或条目数。
   - 对照 `reference_blueprint.emoji_pattern` 判断叙事分散型、清单连续型或混合型。叙事参考在句中、句末或转折处使用 Emoji 时，逐个核对符号的相邻语义锨点和相对位置；若成稿把符号全部机械移到自然段开头、句号前或自然段末尾，必须以 `PERSONA_STYLE_MISMATCH` 阻断。报价、材料、步骤或改造清单参考连续使用行首 Emoji 时，应判定为合理的信息导航，不得因为符号连续就阻断。
   - 原创和仿写都按当前正文检查 Emoji 功能覆盖，不按总数或参考密度判定完成：已有痛点、情感变化或人设判断是否有贴切表达，面积/预算/时间等实际数据类别是否分别导航，已有事项列表是否逐项或按明确同类分组导航，提醒与互动是否落在相邻语义处。没有对应内容、渠道或用户明确禁用时不要求使用；专业人设、参考无表情不构成豁免。
   - 正文包含上述多类功能却只有场景、确认、私信三个装饰，或数据/事项全靠一个 Emoji 代表、情绪表达只用数据图标冒充时，以 `PERSONA_STYLE_MISMATCH` 阻断；`location` 指向遗漏的实际短语或条目，`suggestion` 给出适合该人设的功能与落点，不下达全篇固定数量要求。补齐只改符号和必要措辞，不改事实、数字、结构顺序或列表类型；不能因符号数量多而删除有用的逐项导航。
   - 对照 `content_brief` 和允许用于标题的 Evidence 逐项检查标题事实槽位。标题把预算扩大为已在预算内完成、把预计工期扩大为已完工、把方案效果扩大为最终结算或出现其他输入外事实时，以 `TITLE_FACT_UNSUPPORTED` 阻断，并精确指出应删除或改写的词；不得仅给出“标题不符合公式”的泛化建议。
   - EvidenceBundle 存在 `metadata.rule_kind=forbidden_replacement_map` 的平台规则时，从其结构化 `value` 读取完整“问题词—常用表达方式”映射，逐项复查最终标题、正文和话题，不得使用 Skill 内置词表或常识猜测替换关系。
   - 最终内容仍含任一问题词时，以 `FACT_CHECK_FAILED` 阻断，并在建议中列出命中的问题词和表内可选表达；候选列表为空时只要求在不改变事实的前提下重写整句，不得建议删除后留下残句或编造表外替代词。
   - 已替换但出现候选堆叠、语法不通、语义错位或业务事实改变时，也以 `FACT_CHECK_FAILED` 阻断。替换用的 Emoji 仅承担敏感表达改写功能，不得误算为爆款蓝图要求的情绪或导航 Emoji。
4. 不调用 `validate_content_facts`，不重复实现敏感词、必填字段、数字来源等确定性校验；把 `validation_report` 作为已有事实合并考虑。
5. 返回 `status`、`checks` 和 `evidence_conflicts`。状态只能为 `passed`、`warning`、`blocked`。
6. 每项检查必须返回 `code`、`status`、`location`、`message`、`evidence_ids`、`suggestion`；不得使用 `level` 代替 `status`。
7. `evidence_ids` 可引用当前冻结 EvidenceBundle 中任何真实存在的证据，包括用于核对结构、节奏和 emoji 模式的 `style_reference`；不得引用未知 Evidence ID。
8. 顶层 `status` 必须与 `checks` 中最严重状态一致：存在 `blocked` 则为 `blocked`，否则存在 `warning` 则为 `warning`，其余为 `passed`。

## 装修工长新版验收

- 策略快照存在 `direction_blueprint` 时，先核对正文可识别信息层是否与 `layer_sequence` 同序且无增删，再核对每层是否满足 `phrase_composition` 的 fixed/all/random、允许词组与组数范围。跨方向词组、项目单价出现证据层、缺层或擅自补层均以 `BODY_FORMULA_MISMATCH` 或 `CONTENT_STRUCTURE_MISMATCH` 阻断。
- 七个方向必须逐行验收：自我介绍→自我介绍，项目单价→人工单价，单价+面积/工种总价/人工+辅材→施工报价+真实报价，工艺展示→工艺展示+工地照片或工艺节点，日常工作→日常工作+工地照片或工艺节点；不得接受语义相近但来自其他行的组合。
- FRB01～FRB09 必须分别按锁定模式验收，不得把不同报价口径或多个正文模式混写。检查标题只突出一个主卖点，并在首屏或正文前两段兑现。
- 逐项核对价格类型、地区、项目、单位、包含范围和同源关系；标准单价不得被写成本案成交或结算，FRB07/FRB08/FRB09 缺少程序校验或合计不一致时以事实不一致阻断。
- 检查工长身份、案例、施工过程、优势、承诺、结果和客户反馈均有冻结证据；相关优势超过三项，或图片被用于推断不可见事实时阻断。
- CTA 必须具体且符合当前业务，不得包含无证据的免费、限时、最低价、质保或响应承诺。

允许用于定点回修的阻断 code 为 `CREATION_TYPE_ALIGNMENT`、`COMPOSITION_ALIGNMENT`、`PERSONA_OPENING`、`PERSONA_CLOSING`、`PERSONA_GROUNDING`、`EMOJI_COVERAGE`、`EMOJI_APPROPRIATENESS`、`EMOJI_RESTRICTIONS`、`TITLE_FORMULA_MISMATCH`、`TITLE_FACT_UNSUPPORTED`、`BODY_FORMULA_MISMATCH`、`CONTENT_STRUCTURE_MISMATCH`、`PERSONA_TONE_MISMATCH`、`PERSONA_STYLE_MISMATCH`、`FACT_CHECK_FAILED`、`FACT_INCONSISTENT`。其他阻断 code 会被视为审核契约错误并停止工作流。

不得用单一综合分数替代问题列表，不得修改原内容。
