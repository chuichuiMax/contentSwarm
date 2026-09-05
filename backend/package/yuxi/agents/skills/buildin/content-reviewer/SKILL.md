---
name: content-reviewer
description: 审核 Yuxi 生成内容的创作手法贯穿、公式执行、事实一致性、人设语气、封禁词替换和风险表达。仅在确定性校验完成后的内容审核节点使用。
---

# 内容审核

1. 当前节点 `payload` 必须包含 `content_draft`、`selected_title`、`content_outline`、`strategy_snapshot`、`validation_report` 和 `evidence_bundle`；缺少任一必需输入时直接报告契约错误，不得猜测补齐。
2. 先确认 `validation_report.status` 为 `passed` 或 `warning`。若它为 `blocked`，返回 `REVIEW_CONTRACT_INVALID`，因为确定性阻断不应进入本节点。
3. 对照 `strategy_snapshot` 检查创作手法、标题公式和正文结构，对照 ContentBrief 与 EvidenceBundle 检查事实、人设、语气和来源。
   - 公式只决定信息顺序，不允许把“旧况、关键数据、过程、结果”等公式步骤写成读者可见的报幕句。
   - 出现“旧况很典型”“关键数据先摊开”“先说背景”“再看过程”“最后看结果”“下面来说”“接下来看看”等元话术，或多个段落使用相同模板句式开场时，必须以 `PERSONA_STYLE_MISMATCH` 阻断并给出直接进入场景或事实的改写建议。
   - 不得因为结构、事实和证据正确，就把明显的提纲填充、审核报告腔或机械连接词判为语气通过。
   - EvidenceBundle 存在 `selected_reference=true` 的爆款结构参考时，必须完整读取其 `reference_blueprint.title_slot_sequence`、`content_block_sequence`、`paragraph_rhythm`、`list_pattern`、`emoji_pattern` 和 `interaction_style`，逐项对照，禁止用审核器自己的通用爆款模板替代冻结蓝图。
   - 逐个检查 `content_block_sequence` 是否在正文中按序可识别，并按 `paragraph_rhythm` 检查真实换行和信息密度；只有蓝图实际要求数据块或列表时才检查这些形式。多个独立信息块被压成一行或结构节点缺失时，以 `CONTENT_STRUCTURE_MISMATCH` 阻断。
   - 列表审核严格服从 `list_pattern`。只有 `type=numbered` 或包含编号的 `mixed` 才要求编号清单，并按蓝图的出现位置和条目节奏检查；`none`、`emoji`、`bulleted` 或不含编号的 `mixed` 不得强制改成 `1–4` 清单。不得设置固定段落数、双换行数或条目数。
   - 对照 `reference_blueprint.emoji_pattern` 判断叙事分散型、清单连续型或混合型。叙事参考在句中、句末或转折处使用 Emoji 时，逐个核对符号的相邻语义锨点和相对位置；若成稿把符号全部机械移到自然段开头、句号前或自然段末尾，必须以 `PERSONA_STYLE_MISMATCH` 阻断。报价、材料、步骤或改造清单参考连续使用行首 Emoji 时，应判定为合理的信息导航，不得因为符号连续就阻断。
   - 对照 `content_brief` 和允许用于标题的 Evidence 逐项检查标题事实槽位。标题把预算扩大为已在预算内完成、把预计工期扩大为已完工、把方案效果扩大为最终结算或出现其他输入外事实时，以 `TITLE_FACT_UNSUPPORTED` 阻断，并精确指出应删除或改写的词；不得仅给出“标题不符合公式”的泛化建议。
   - EvidenceBundle 存在 `metadata.rule_kind=forbidden_replacement_map` 的平台规则时，从其结构化 `value` 读取完整“问题词—常用表达方式”映射，逐项复查最终标题、正文和话题，不得使用 Skill 内置词表或常识猜测替换关系。
   - 最终内容仍含任一问题词时，以 `FACT_CHECK_FAILED` 阻断，并在建议中列出命中的问题词和表内可选表达；候选列表为空时只要求在不改变事实的前提下重写整句，不得建议删除后留下残句或编造表外替代词。
   - 已替换但出现候选堆叠、语法不通、语义错位或业务事实改变时，也以 `FACT_CHECK_FAILED` 阻断。替换用的 Emoji 仅承担敏感表达改写功能，不得误算为爆款蓝图要求的情绪或导航 Emoji。
4. 不调用 `validate_content_facts`，不重复实现敏感词、必填字段、数字来源等确定性校验；把 `validation_report` 作为已有事实合并考虑。
5. 返回 `status`、`checks` 和 `evidence_conflicts`。状态只能为 `passed`、`warning`、`blocked`。
6. 每项检查必须返回 `code`、`status`、`location`、`message`、`evidence_ids`、`suggestion`；不得使用 `level` 代替 `status`。
7. `evidence_ids` 可引用当前冻结 EvidenceBundle 中任何真实存在的证据，包括用于核对结构、节奏和 emoji 模式的 `style_reference`；不得引用未知 Evidence ID。
8. 顶层 `status` 必须与 `checks` 中最严重状态一致：存在 `blocked` 则为 `blocked`，否则存在 `warning` 则为 `warning`，其余为 `passed`。

允许用于定点回修的阻断 code 为 `TITLE_FORMULA_MISMATCH`、`TITLE_FACT_UNSUPPORTED`、`BODY_FORMULA_MISMATCH`、`CONTENT_STRUCTURE_MISMATCH`、`PERSONA_TONE_MISMATCH`、`PERSONA_STYLE_MISMATCH`、`FACT_CHECK_FAILED`、`FACT_INCONSISTENT`。其他阻断 code 会被视为审核契约错误并停止工作流。

不得用单一综合分数替代问题列表，不得修改原内容。
