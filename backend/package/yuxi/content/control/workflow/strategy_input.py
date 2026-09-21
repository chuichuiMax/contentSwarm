"""联合策略模型视图：保留决策语义与原始证据路径，审计数据不进入提示。"""

from copy import deepcopy

from yuxi.content.model.contracts.content_nodes import JointStrategyPromptV1
from yuxi.content.v3.modular_rules import runtime_policy


async def load_strategy_profiles(repo, locked_versions: dict) -> tuple[dict, dict]:
    channel = {}
    channel_id = locked_versions.get("channel_profile_version_id")
    if channel_id:
        channel = await repo.get_channel_strategy_profile(channel_id)
        if channel is None:
            raise ValueError("任务锁定的渠道版本不存在")
    persona = {}
    persona_id = locked_versions.get("persona_profile_version_id")
    if persona_id:
        row = await repo.get_persona_version(persona_id)
        if row is None:
            raise ValueError("任务锁定的人设版本不存在或已删除")
        version, _ = row
        persona = {
            "version_id": version.id,
            **{
                key: deepcopy(getattr(version, key))
                for key in (
                    "identity",
                    "experience_facts",
                    "professional_background",
                    "tone",
                    "values",
                    "positions",
                    "service_boundaries",
                    "forbidden_phrases",
                    "evidence_ids",
                )
            },
        }
    return channel, persona


def project_strategy_input(payload: dict, *, channel_profile: dict, persona_profile: dict) -> dict:
    # 完整输入先由 JointStrategyInputV1/ReevaluateJointStrategyInputV1 校验并保留。
    view = deepcopy(payload)
    if view["runtime_config_snapshot"].get("creation_mode") != "viral_rewrite":
        raise ValueError("内容策略只支持爆款仿写")
    runtime_snapshot = view["runtime_config_snapshot"]
    view["runtime_config_snapshot"] = {
        "creation_mode": "viral_rewrite",
        "reference_policy": runtime_policy(runtime_snapshot, "viral-author-core", "reference_policy"),
        "price_policy": runtime_policy(runtime_snapshot, "viral-price-author", "price_policy"),
    }
    view["channel_profile"] = deepcopy(channel_profile)
    view["persona_profile"] = deepcopy(persona_profile)
    brief = view["content_brief"]
    if persona_profile:
        brief.pop("persona", None)
    for key in list(brief):
        if key.endswith("_version_id") or key in {"task_id", "visual_material", "attachments", "mode"}:
            del brief[key]
    user_request = str(brief.get("user_request") or "").strip()
    bundle = view["evidence_bundle"]
    view["evidence_bundle"] = {key: value for key, value in bundle.items() if key in {"items", "status"}}
    items = bundle.get("items", [])
    for section in ("form_values", "business_variables"):
        values = brief.get(section) or {}
        for key, value in list(values.items()):
            if key.endswith("_version_id") or key in {"visual_material", "attachments"}:
                del values[key]
            elif key == "user_request" and user_request and str(value).strip() == user_request:
                # 单输入框简报会为兼容旧协议保存三份同值；模型视图只保留顶层事实。
                del values[key]
            elif any(
                item.get("source_type") == "manual_input"
                and item.get("source_id", "").startswith("field_")
                and item.get("verified_status") == "user_confirmed"
                and key in item.get("variable_codes", [])
                and type(item.get("value")) is type(value)
                and item.get("value") == value
                for item in items
            ):
                # 只移除有同源、同变量、同值证据的简报副本；原证据数组绝不重排。
                del values[key]
    paths = []
    for index, item in enumerate(items):
        for key in ("source_hash", "source_version", "created_at"):
            item.pop(key, None)
        if (
            item.get("value") not in (None, "", [], {})
            and item.get("evidence_type", item.get("type")) != "style_reference"
            and (item.get("metadata") or {}).get("material_type") != "viral_example"
        ):
            item["input_path"] = f"evidence_bundle.items.{index}.value"
            paths.append(item["input_path"])
    if user_request:
        paths.append("content_brief.user_request")
    paths.extend(
        f"content_brief.{section}.{key}"
        for section in ("form_values", "business_variables")
        for key, value in (brief.get(section) or {}).items()
        if value not in (None, "", [], {})
    )
    candidates = view["strategy_candidates"]
    candidates["available_input_paths"] = paths
    candidates.pop("reference_candidate_limit", None)
    candidates.pop("selection_skill", None)
    options = candidates.get("direction_options") or []
    if (
        candidates.get("auto_direction")
        and len(options) == 1
        and candidates.get("direction_blueprint") == options[0].get("direction_blueprint")
    ):
        # 自动方向的唯一候选已经携带完整蓝图，避免在同一提示中重复发送。
        candidates.pop("direction_blueprint", None)
    if candidates.get("industry_slug") == "decoration":
        for section in ("title_formulas", "content_formulas"):
            for item in candidates[section]:
                source = item.get("source_content")
                if isinstance(source, dict):
                    source.pop("cross_industry", None)
    view["reference_candidates"] = [
        {
            key: value
            for key, value in item.items()
            if key
            in {
                "id",
                "title",
                "industry_slug",
                "source_hash",
                "reference_card",
                "structure_preview",
            }
        }
        for item in view["reference_candidates"]
    ]
    return JointStrategyPromptV1.model_validate(view).model_dump(mode="json", exclude_none=True)
