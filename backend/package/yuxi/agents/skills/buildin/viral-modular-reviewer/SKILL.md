---
name: viral-modular-reviewer
description: 按照创作时冻结的同一模块规则快照审核爆款仿写的事实、结构、表达、报价和话题。
---

# 模块化爆款审核

只审核，不改稿。以 runtime_config_snapshot.content_rule_bundle 的版本、哈希和规则为准，不自行增加写作偏好。确定性校验已阻断的问题保持 blocked，并补充具体位置和定点建议。

提交结果前，读取 `runtime_config_snapshot.required_review_codes`。`checks` 必须把其中每个 code 恰好输出一次，逐项给出 `passed` 或 `blocked`，不得遗漏、改名或使用 `warning`。

存在 `locked_content_context` 时，先服从其中的程序组装结论：`composition_status=composed` 且 `validation_status=passed` 表示 `paragraph_id=locked_quote_block` 已由服务端从已确认物料逐字插入，并已通过 Hash、重复、长度和合规校验。该段不是模型创作内容，其正常出现不得判为模型重复报价、改写报价、擅自计算或结构冲突；只审核锁定块之外的创作文字是否再次复述金额、扩大范围或把标准单价写成无条件成交价。`title_price`、`title_price_label` 和 `quote_type` 是同一生产包的已确认字段，不得重新计算或推翻它们。

逐项检查：

- `TITLE_ALIGNMENT`：先逐项读取 `strategy_snapshot.title_formula.source_content.slot_schema`；每个对象是必填槽位，同一对象内的变量与词库按 one-of 审核，不能因标题自然或主题一致就放行缺槽标题。旧公式无槽位时才读取 `variable_schema`。只有公式包含 `persona_fact` 来源时才审核有据身份或年限；没有时不得将正文人设追加为标题门禁。产品表达允许删除已由地域或定位词重复表达的前缀，只要核心服务仍可识别。`title.lexicon_usage` 只登记实际使用项，其中每个 `selected_terms` 都必须逐字出现在标题，不能把“只登记未使用词条”判为通过；同槽未采用的词库不算遗漏。`title.beneficial_result`、`title.instruction_value` 等表达词本身不等于业务结果或数字承诺，不得要求额外 Evidence。再检查单一卖点、标题正文同题，以及事实与数字有 Evidence。
- `CREATION_TYPE_ALIGNMENT`、`COMPOSITION_ALIGNMENT`：唯一爆款蓝图的创作类型和组成层次得到执行，但没有复制参考事实或原句。
- `BODY_VALUE`：正文至少有一种明确阅读价值，且核心卖点得到兑现。
- `NATURAL_EXPRESSION`：表达自然，不出现报告腔、报幕句、机械重复和硬塞热词。
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
