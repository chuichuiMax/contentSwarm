from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from yuxi.content.control.errors import ContentApplicationError
from yuxi.content.model.contracts import get_input_contract_model


@dataclass(frozen=True, slots=True)
class ContentNodeInputAssembly:
    contract_name: str
    payload: dict[str, Any]
    snapshot_hash: str


class ContentNodeInputAssembler:
    """按发布工作流声明投影 Agent 可见状态，并在委派前完成严格契约校验。"""

    @staticmethod
    def build(*, node: dict[str, Any], state: dict[str, Any]) -> ContentNodeInputAssembly:
        contract_name = str(node.get("input_contract") or "")
        required_fields = tuple(node.get("state_inputs") or ())
        optional_fields = tuple(node.get("optional_state_inputs") or ())
        missing = [field for field in required_fields if field not in state or state.get(field) is None]
        if missing:
            raise ContentApplicationError(
                "node_input_missing",
                f"节点 {node['id']} 缺少上游状态: {', '.join(missing)}",
                "invalid",
            )
        raw_payload = {field: state[field] for field in required_fields}
        raw_payload.update({field: state.get(field) for field in optional_fields})
        if contract_name == "SemanticReviewInputV1":
            raw_payload.update(
                review_scope=(
                    "full"
                    if (state.get("runtime_config_snapshot") or {}).get("strict_semantic_review")
                    else "expression"
                ),
                channel_profile=state.get("channel_profile") or {},
                persona_profile=state.get("persona_profile") or {},
            )
        try:
            payload = get_input_contract_model(contract_name).model_validate(raw_payload).model_dump(mode="json")
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            field_path = ".".join(str(item) for item in first.get("loc") or [])
            message = str(first.get("msg") or "节点输入不符合契约")
            raise ContentApplicationError(
                "node_input_invalid",
                f"节点 {node['id']} 输入不符合 {contract_name}: {field_path} {message}".strip(),
                "invalid",
            ) from exc
        if contract_name in {"JointStrategyInputV1", "ReevaluateJointStrategyInputV1"}:
            # 路径紧邻事实值，模型无需自行计数；只标注可见副本，不改冻结证据。
            for index, item in enumerate(payload["evidence_bundle"].get("items", [])):
                if (
                    item.get("value") not in (None, "", [], {})
                    and item.get("evidence_type", item.get("type")) != "style_reference"
                    and (item.get("metadata") or {}).get("material_type") != "viral_example"
                ):
                    item["input_path"] = f"evidence_bundle.items.{index}.value"
        canonical = json.dumps(
            {"contract": contract_name, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ContentNodeInputAssembly(
            contract_name=contract_name,
            payload=payload,
            snapshot_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


__all__ = ["ContentNodeInputAssembler", "ContentNodeInputAssembly"]
