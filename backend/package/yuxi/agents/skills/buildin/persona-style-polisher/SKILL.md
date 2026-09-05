---
name: persona-style-polisher
description: 按 PersonaProfile 优化语气和表达指纹，同时保持事实、数字、证据引用、人物经历和服务边界不变。
version: 1.1.0
version: 1.0.0
---

# Persona 风格润色

你只能基于当前节点 `payload.content_draft` 和 `payload.persona_profile` 改变措辞、句式、节奏和语气，不能改变事实层。

- 不增加、删除或改写价格、参数、数字、结果、承诺和人物经历。
- 不新增 EvidenceBundle 之外的 Evidence ID。
- 遵守 Persona 的偏好表达、禁用表达、价值观和服务边界。
- 保持已锁定标题、内容结构和主叙事轴一致。
- 输出前核对所有阿拉伯数字均来自数字白名单。
- 严格提交 `PersonaPolishResultV1`：`polished_body`、`change_summary`、`preserved_fact_checks`，不解释工作过程。
