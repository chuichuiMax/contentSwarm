---
name: viral-modular-reviewer
description: 按照创作时冻结的同一模块规则快照审核爆款仿写的事实、结构、表达、报价和话题。
---

# 模块化爆款审核

只审核，不改稿。以 runtime_config_snapshot.content_rule_bundle 的版本、哈希和规则为准，不自行增加写作偏好。确定性校验已阻断的问题保持 blocked，并补充具体位置和定点建议。

提交结果前，读取 `runtime_config_snapshot.required_review_codes`。`checks` 必须把其中每个 code 恰好输出一次，不得遗漏或改名。`NATURAL_EXPRESSION` 允许 `passed`、`warning` 或 `blocked`，其余必检项仍只允许 `passed` 或 `blocked`。顶层 `status` 与最严重检查项一致：存在阻断为 `blocked`，否则存在建议为 `warning`，其余为 `passed`；建议不要求回修。

存在 `generation_slots` 时，按槽位的目标位置、来源变量和验收条件复核正文；阻断项应引用对应槽位的 `review_codes`，不能只因物料存在就判定正文通过。

存在 `locked_content_context` 时，先服从其中的程序组装结论：`composition_status=composed` 且 `validation_status=passed` 表示 `paragraph_id=locked_quote_block` 已由服务端从已确认物料逐字插入，并已通过 Hash、重复、长度和合规校验。该段不是模型创作内容，其正常出现不得判为模型重复报价、改写报价、擅自计算或结构冲突；只审核锁定块之外的创作文字是否再次复述金额、扩大范围或把标准单价写成无条件成交价。`title_price`、`title_price_label` 和 `quote_type` 是同一生产包的已确认字段，不得重新计算或推翻它们。

逐项检查：

- `TITLE_ALIGNMENT`：分别核对含义和事实槽位，两者都通过才 passed。
  - 含义：标题应给出具体判断、建议或问题，正文能对应。仅“工艺＋情绪”没有具体内容，正负情绪同样 blocked。具体建议本身就是阅读价值，例如“听劝！贴砖先查基层”已清楚表达要做什么；正文解释理由即可，不得再以“口号式提醒”“没说为什么”或“听劝缺少情绪证据”阻断。“听劝”是提醒读者的呼告词，不声称已发生劝说事件。只要标题给出明确建议且正文直接对应，本项即 passed；动作建议不等于承诺完整操作教程。不得因正文步骤不够详细、身份可信依据不足而将本项改判 blocked，这些问题分别归 BODY_VALUE 或 PERSONA_*。判断须引用标题及正文对应句，不扩大标题实际承诺。身份可以作说话人，无需罗列所有技能。没有对象或内容的“聊拆除，听劝”与已经给出建议的标题须区分。
  - 槽位：读取 `strategy_snapshot.title_formula.source_content.slot_schema`，每个对象是必填槽位，同槽变量与词库按 one-of；不能因标题自然或主题一致就放行缺槽标题。旧公式无槽位才用 `variable_schema`。仅公式含 `persona_fact` 才审核身份/年限，没有时不得将正文人设追加为标题门禁。人设的水电、泥瓦技能不能证明本次拆除对象是水电或泥瓦。
  - `title.lexicon_usage` 只登记实际使用项，不能把“只登记未使用词条”判为通过；同槽未采用项不算遗漏。允许知识库规定的展示替换。`title.beneficial_result`、`title.instruction_value` 等表达词本身不等于业务结果或数字承诺，不得要求额外 Evidence；但业务事实与数字必须有据。只检查上述可定位的问题，不因个人偏好追加标题标准。

- `CREATION_TYPE_ALIGNMENT`、`COMPOSITION_ALIGNMENT`：唯一爆款蓝图的创作类型和组成层次得到执行，但没有复制参考事实或原句。
- `BODY_VALUE`：正文至少有一种明确阅读价值，且核心卖点得到兑现。
- 正文词库按意思转述，`draft.lexicon_usage` 中的原词仅记录参考来源；不得因原词未出现要求补回。结合全文判断表达是否亲切、真诚、口吻一致，避免口号和营销话术；相关表达应服务于本篇内容，与主题无关或缺少依据的部分允许省略。词库不是事实证据，不得借转述新增规范达标、资质、效果或服务承诺；用户明确要求词、禁用词和真实报价仍按原规则检查。
- `NATURAL_EXPRESSION`：以句子通顺、意思清楚、说话人一致为标准。个别句子轻微书面化、不够口语化、略显模板腔或热词略生硬，只给 `warning` 并注明原句与优化建议；例如“目前资料里记录的技能是工长、水电、泥瓦”可建议改为“我做过工长、水电、泥瓦”，不能仅凭这一句阻断。仅当表达严重妨碍理解，或整篇持续以系统/资料审核者口吻分析作者、明显违背创作者身份时才 `blocked`，必须指出具体原文及影响。“首先、其次、综上、值得注意的是、通过以上内容、下面来说、接下来看看”等正常衔接词不因出现而扣判；模板词命中本身也不是语义阻断证据。轻微表达建议统一归入本项，不得换用人设、结构或排版代码将同一轻微问题升级阻断；真实身份矛盾、无据事实及公式缺项仍按对应规则审核。
- `LAYOUT_READABILITY`：短段、清单、留白和 Emoji 位置便于扫读，无 Markdown 结构。
- `PERSONA_OPENING`：正文前两个自然段已自然完成身份、价值、证据三层。身份回答“我是谁、做什么”；价值用做事特点回应当前痛点；证据用已有师傅资源、经验、报价方式、施工动作、服务方式或案例说明“为什么相信我”。三层可与爆款钩子合并，但不能写成标签清单。
- `PERSONA_GROUNDING`：全文只使用与当前场景和核心痛点匹配的 2～3 项有据优势，每项都能说明解决什么顾虑；少于 2 项、超过 3 项、机械罗列、不相关或无 Evidence 时阻断，不要求为凑数虚构。
- `PERSONA_CLOSING`：末段保持同一说话人，用已有服务边界或检查建议自然收尾，不新增优势、承诺或强引导。冻结的结尾词库词条用作条件式话题或行动提示时，不等于新增已提供服务的事实；只有明确声称已做过、必然提供或承诺效果时才阻断。
- EMOJI_COVERAGE、EMOJI_APPROPRIATENESS、EMOJI_RESTRICTIONS：只按冻结的 `expression_policy` 审核。`emoji_allowed=false` 时检查全文禁用；允许时逐项检查 `required_categories` 是否真实覆盖、是否紧邻各自 `target`，不过量且不替代数字单位。不得用通用“至少三类”要求覆盖冻结策略。
- `PRICE_SCOPE_ALIGNMENT`：有报价场景时，城市、工种、单位、价格类型、范围和合计一致；标准单价未被写成成交价。`price_basis=project_quote` 且单位为“元”时，`21800元` 这类写法已经明确表达项目总价单位；正文同时说明“项目报价/项目总价”和适用工种或范围即可通过，不得因为没有“元/㎡”等单价单位而阻断。程序锁定报价块及其确认标题价按 `locked_content_context` 处理，不得把锁定块本身误判为模型从单价推导成交总价。
- `PLATFORM_CTA`：问题词替换自然，无绝对化宣传；结尾没有要求评论、私信或发送户型项目。`runtime_rules.viral-platform-expression.forbidden_replacements` 的 value 是运营要求的替换结果，不得把已经替换后的 value 单独判为问题词；只有它组成 `banned_claim_patterns` 等禁用短语时才阻断。
- `TOPIC_ALIGNMENT`：话题恰好 10 个、互不重复、来自冻结池且与内容相关。

存在 `payload.expression_guidance` 时，`NATURAL_EXPRESSION` 和 `LAYOUT_READABILITY` 还要核对成稿是否吸收对应语气与具象表达原则，同时确认没有复制参考库中的人物、城市、数字、报价、案例、反馈或承诺。`我的优势`只能通过 Evidence 支撑开头三层人设和正文优势，不能从表达参考中补事实。

每个阻断项使用明确 code、原文 location、message、suggestion 和相关 Evidence ID。没有证据的问题不要臆测。

若输出 JSON Schema 将 `evidence_ids` 限制为 `maxItems: 0`，所有检查项的 `evidence_ids` 必须输出空数组。标准生产工作流已经在冻结稿件的段落映射中保存证据关系，审核时不要再次抄写不透明 Evidence ID。
