---
name: viral-content-reviewer
description: 一次审核爆款仿写成稿的结构、事实与报价口径、首尾人设、语义 Emoji 和自然表达，并给出可定点回修的问题。
---

# 爆款仿写审核

只审查本次 `payload` 中的最终 `selected_title`、`content_outline`、`content_draft`，不改稿、不调用知识库或 `validate_content_facts`，不增加审核节点。`validation_report` 应已通过确定性校验；若为 `blocked`，报告契约错误，不猜测补齐。锁定策略、`direction_blueprint`、标题与正文公式、`body_calling`、`channel_profile`、`persona_profile` 和冻结 Evidence 是判定依据。爆款参考只提供结构、节奏和互动方式，不是本篇事实来源。`review_scope` 不能用来跳过下列仿写质量检查。

## 必须逐项返回的检查

无论通过或阻断，`checks` 都分别包含下面八项；不存在方向蓝图时，后两项返回 `passed` 并说明不适用，不虚构规则。

- `EMOJI_COVERAGE`：根据最终正文实际出现的痛点/疑问、人物判断/情绪变化、面积/预算/时间等不同数据类别、独立事项/步骤、提醒和互动检查功能覆盖。报价工种、材料或验收动作逐项导航，同类数据可清晰分组；一枚场景或私信符号不能覆盖所有类别。不适用的功能不要求补造。
- `EMOJI_APPROPRIATENESS`：符号须邻接实际语义，符合人物气质；错配、无意义重复、挡阅读的堆叠或机械全放段首/句末时阻断。按参考判断叙事分散、清单连续或混合排布；清单的连续行首符号是合理导航，不能只因连续就阻断。
- `EMOJI_RESTRICTIONS`：用户和渠道禁用、少量或克制要求优先。明确禁用时不要求功能覆盖，同时阻断实际出现的 Emoji；参考自身少表情或无表情不构成本篇禁令。普通项目符号、编号和封禁词替换用 Emoji 不能冒充内容功能。
- `PERSONA_OPENING`：首个非空自然段的钩子中自然融入主题相关的已知身份，以及有据的经验或擅长领域，并承接读者问题。不要求固定句型或全部履历。
- `PERSONA_CLOSING`：最后一个非空自然段用已有服务优势或范围承接本篇，给出允许且具体的行动邀请；空泛“欢迎咨询”、重复首段或无据的免费/限时/质保承诺须阻断。
- `PERSONA_GROUNDING`：身份、年限、城市服务范围、技能、团队、亲历、优势与承诺必须来自当前人设/冻结事实。案例城市不等于服务城市，工长身份不证明亲自施工，语气偏好不等于客户评价。
- `CREATION_TYPE_ALIGNMENT`：`content_brief.content_type_code`、锁定方向、标题/正文公式和参考类型一致；项目单价、单价+面积、工种总价、人工+辅材不能互换。方向蓝图规定的模式优先于参考。
- `COMPOSITION_ALIGNMENT`：在正文中逐层定位 `direction_blueprint.layer_sequence`，按 `phrase_composition` 核对 `fixed/all/random`、允许组与组数。不得跨方向借组、缺层或补层；项目单价无独立证据层，施工报价需要真实报价，工艺/日常工作需要相应已有工地或工艺依据。缺资料应指明缺口，不要求编造。

用户明确不自述、第三人称、纯清单、禁止营销/互动，或资料不足、锁定结构不允许时，对相应首尾项返回 `passed` 并写明具体不适用原因。部分资料只按已有信息审核。上述八项只能为 `passed` 或 `blocked`；实际遗漏或违反限制不以 `warning` 放行。

## 同次审核的结构、报价与事实

- 参考证据有 `selected_reference=true` 时，逐项比较最终标题的 `title_pattern`/`title_slot_sequence`，首段 `opening_hook`，正文 `content_block_sequence`、`paragraph_rhythm`、`list_pattern`、情绪推进、`emoji_pattern` 和结尾 `interaction_style`。只检查蓝图实际要求的列表和节奏；无编号参考不得强迫改为固定编号。缺块、块顺序错误、多个事项压成长段或钩子退化时用 `CONTENT_STRUCTURE_MISMATCH` 定位。参考原文事实、数字、人物、原句或句子骨架被移植时按 `FACT_INCONSISTENT` 阻断。
- 标题从有据的地域、业务、房屋、需求、风格、人群、价格、结果、身份词中择二至四个核心元素，自荐态度和受众进入标题时也分别计入，城市名与“本地”只计一个地域元素。标题只有一个主卖点并在首屏或前两段兑现，还必须是可自然朗读的中文短句；超过四类元素、地域重复或多个名词直接串接造成关键词堆砌时，以 `TITLE_FORMULA_MISMATCH` 阻断并建议删减或用动作词连接。证据不足时不为凑元素编造事实。事实槽位不得把预算说成已完成、预计说成已完工、方案说成最终结算。公式不符或标题正文主题脱节同样使用 `TITLE_FORMULA_MISMATCH`，无据事实用 `TITLE_FACT_UNSUPPORTED`。正文按锁定 `body_calling.sections` 顺序、变体和词库执行，`FRB01`～`FRB09` 不混模式；在事实允许时体现钩子、案例背景、业主需求、解决方案、报价、施工/工艺、人设优势、结果/反馈与 CTA，不为凑段落编造缺失信息；缺层或错层用 `BODY_FORMULA_MISMATCH` 或 `CONTENT_STRUCTURE_MISMATCH`。
- 每条实际写出的价格逐项核对冻结证据的**类型、项目、地区、单位、包含范围、来源与同源关系**。标准单价参考可独立展示或与项目预算并列，无工程量、小计、全部类别或与预算不加总一致时不因此阻断。标准价写成本案成交、实付、结算或最终报价，预算写成结算，缺少程序校验却自行乘算或拼凑合计，均以 `FACT_INCONSISTENT` 阻断并引用具体句子和证据。不得通过增加实际报价要求来掩盖现有标准价。
- 客户评价、结果、个人亲历、服务承诺和图片推断必须有冻结依据；图片只能证明可见场景、阶段、工种和细节。报价清单优先本场景常见项目，不堆砌冷门商品；成稿至少提供价格参考、装修判断、预算判断、避坑、真实案例、方案或施工知识之一。根据场景顾虑选择与本篇相关的二至三项有据优势并证明，不堆砌无关卖点。CTA 给出具体且有依据的咨询理由，符合业务与渠道；不补无证据看工地、免费、最低价、限时、质保或响应承诺。
- 输出前在本次检查内核对数字与输入、同源价格合计、城市和业务、输入外事实、未经验证的优势承诺，以及标题与正文的核心主题；分别按 `FACT_INCONSISTENT`、`FACT_CHECK_FAILED`、`PERSONA_GROUNDING` 或 `TITLE_FORMULA_MISMATCH` 定位实际问题，不新增审核项或模型调用。
- 有 `metadata.rule_kind=forbidden_replacement_map` 时，仅用其 `value` 的问题词—常用表达映射复查最终标题、正文和话题。只有问题词的 Unicode 字符在原文中逐字连续出现才算命中；不得因同音、近义、相关概念或原文不存在的字阻断，`location` 和 `message` 必须引用实际连续命中的原文。仍出现问题词、候选串接、语法不通、语义错位或事实改变，以 `FACT_CHECK_FAILED` 阻断；候选为空时只建议事实不变地重写整句，不发明表外替代词。
- 标题、正文和话题若出现服务端一律阻断的绝对化词 `保证`、`百分百`、`100%`、`一定有效`、`绝对`、`零风险`、`最便宜`或排名意义的`第一`，按 `FACT_CHECK_FAILED` 定位；不得因为用户资料里有原词就放行。
- 公式名称、结构段任务或“先说背景”“关键数据先摊开”“再看过程”“最后看结果”等报幕句进入正文，或多段使用同样模板句式，即使事实正确也以 `PERSONA_STYLE_MISMATCH` 阻断。自然度检查不能要求重写整篇而破坏正确结构。

## 返回与回修定位

调用 `submit_content_node_result` 提交 `status`、`checks`、`evidence_conflicts`。每项 check 必有 `code`、`status`、`location`、`message`、`suggestion`、`evidence_ids`；只引用当前冻结证据的真实 ID，无引用用 `[]`。有 blocked 时顶层 blocked，否则有 warning 时 warning，其余 passed；没有真实冲突时 `evidence_conflicts=[]`。不得只返回综合分数或分析文字。

阻断 `location` 指具体原文短语、条目、首段/末段或缺失块；`suggestion` 说明用哪项当前证据、在何处做最小修改。纯 Emoji 问题只增删移动符号及必要空格，保持文字、数字、单位、标点、标题、大纲和段落顺序；首尾人设问题只改对应位置，保留无关中段；报价问题指出正确价格类型和范围，不虚构价格。可用的阻断 code 仅为 `CREATION_TYPE_ALIGNMENT`、`COMPOSITION_ALIGNMENT`、`PERSONA_OPENING`、`PERSONA_CLOSING`、`PERSONA_GROUNDING`、`EMOJI_COVERAGE`、`EMOJI_APPROPRIATENESS`、`EMOJI_RESTRICTIONS`、`TITLE_FORMULA_MISMATCH`、`TITLE_FACT_UNSUPPORTED`、`BODY_FORMULA_MISMATCH`、`CONTENT_STRUCTURE_MISMATCH`、`PERSONA_TONE_MISMATCH`、`PERSONA_STYLE_MISMATCH`、`FACT_CHECK_FAILED`、`FACT_INCONSISTENT`。
