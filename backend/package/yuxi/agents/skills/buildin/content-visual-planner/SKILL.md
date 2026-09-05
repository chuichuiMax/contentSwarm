---
name: content-visual-planner
description: 使用已审批内容、已冻结证据和渠道规范制定结构化视觉方案。
version: 1.4.0
---

# 视觉方案规划

- 只读取当前节点 `payload`，仅使用其中已审批的标题、正文、锁定策略、证据、品牌素材和渠道规范，不调用业务事实或知识库工具。
- 严格提交 `VisualPlanResultV1`：`size`、`safe_area`、`text`、`source_asset_ids`、`mode`、`risks`、`artifact_version_id`、`evidence_ids`。
- `media_evidence_items` 中 `selected_for_cover=true` 的图片是用户在素材库中锁定的唯一封面原图；`source_asset_ids` 必须且只能填写该图片的 `id`，不得省略或替换。
- 图片文件名、素材显示名不是图片内容证据，不得据此识别城市、项目、品牌、人物或场景，也不得由此新增 `risks`。
- 素材有效性、权限和用户确认状态已在内容生成前完成；本节点只制定版式与封面文案，不重复做素材语义一致性判断。
- 文字不得新增未经证据支持的价格、参数、效果或承诺。
- 如果 `payload.runtime_config_snapshot.visual_material.hycanvas_fillable_fields` 存在，只读取非 `label` 字段：`text[0]` 必须满足所有 `title` 字段中最小的 `maxChars`，`text[1]` 必须满足所有 `subtitle`、`body_excerpt` 字段中最小的 `maxChars`。标签保留模板原文，不为标签生成替换内容。
- 封面文案超过字段上限时，必须改写成符合 `maxChars` 的自然短句：保留原文的核心对象、关键事实、数字及表达意图，优先删去修饰词、重复信息和弱语气词，再做同义压缩；不得机械截断、留下残句、改变事实含义或新增无证据结论。
- 短文案仍须语义完整、读起来通顺，并与对应字段职责一致：`title` 表达核心主题，`subtitle`、`body_excerpt` 补充一个最重要的信息点，不把不同字段内容强行拼接。
- 字段提供 `maxCharsPerLine`、`maxLines` 时，须在不拆开数字、单位和完整词组的语义边界插入换行符；每行和总行数都必须符合限制，禁止依赖画布裁切处理横向溢出。
- 提交结果若返回 `visual_text_too_long`，必须根据错误中的字段位置和最大字数立即完成上述语义压缩，并在当前节点重新调用结果提交工具；不得原样重交、只删除末尾字符或把字数错误留给用户处理。
- 缺少必要素材时明确阻断，不得用未授权资产替代。
