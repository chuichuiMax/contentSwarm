"""正文模型视图：审计快照保留在服务端，模型只读取本次创作所需内容。"""

from copy import deepcopy

from yuxi.content.model.contracts.content_nodes import GenerateContentPromptV1


def project_generation_input(payload: dict) -> dict:
    # 调用方必须先完成 GenerateContentInputV1 校验（包括冻结策略 hash）。
    projected = deepcopy(payload)
    strategy = projected["strategy_snapshot"]
    strategy.pop("decision", None)
    strategy["source_snapshot_hash"] = strategy.pop("snapshot_hash")
    projected["runtime_config_snapshot"] = {
        "creation_mode": payload["runtime_config_snapshot"].get("creation_mode", "original"),
    }
    brief = projected["content_brief"]
    brief.pop("visual_material", None)
    for key in list(brief):
        if key.endswith("_version_id"):
            del brief[key]
    for section in ("business_variables", "form_values"):
        for key in list(brief.get(section) or {}):
            if key.endswith("_version_id") or key in {"attachments", "visual_material"}:
                del brief[section][key]
    for item in projected["evidence_bundle"].get("items", []):
        for field in ("source_hash", "source_version", "created_at"):
            item.pop(field, None)
    return GenerateContentPromptV1.model_validate(projected).model_dump(mode="json")
