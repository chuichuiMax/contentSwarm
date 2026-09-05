---
name: content-strategy-planner
description: 根据 SOP1 输入和正式规则库，一次选择内容方向、创作手法、一个标题公式和一个正文公式。
version: 4.0.1
---

# V3 简化内容策略选择

只处理当前策略节点，不决定下一节点或修改工作流。

1. 使用 `payload.rule_version_id` 调用一次 `get_creation_rule_bundle`，读取任务锁定的正式规则；不得凭记忆补公式。
2. 综合 `content_brief` 与已有 `evidence_bundle`，选择一个内容方向，并从固定硬约束通过的组合组中选择最合适的一组；候选排序仅是推荐顺序，不要求选择第一名。
3. `creation_method_codes` 必须完整保持组合组内的手法及顺序；标题公式和正文公式各选择一个。
4. 只提交规则工具返回的 ID，不生成标题或正文，不编造缺失事实。
5. 严格提交 `CreationStrategySelectionResultV1`。

选择结果会由固定规则再次校验并锁定；选择任一通过硬约束的组合组均合法，任何候选集合外的 ID 都会阻断。
