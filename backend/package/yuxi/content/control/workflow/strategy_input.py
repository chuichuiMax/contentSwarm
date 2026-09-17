"""联合策略模型视图：保留决策语义与原始证据路径，审计数据不进入提示。"""

from copy import deepcopy

from yuxi.content.model.contracts.content_nodes import JointStrategyPromptV1


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
    mode = view["runtime_config_snapshot"].get("creation_mode", "original")
    view["runtime_config_snapshot"] = {"creation_mode": mode}
    view["channel_profile"] = deepcopy(channel_profile)
    view["persona_profile"] = deepcopy(persona_profile)
    brief = view["content_brief"]
    if persona_profile:
        brief.pop("persona", None)
    for key in list(brief):
        if key.endswith("_version_id") or key in {"task_id", "visual_material", "attachments", "mode"}:
            del brief[key]
    bundle = view["evidence_bundle"]
    view["evidence_bundle"] = {key: value for key, value in bundle.items() if key in {"items", "status"}}
    items = bundle.get("items", [])
    for section in ("form_values", "business_variables"):
        values = brief.get(section) or {}
        for key, value in list(values.items()):
            if key.endswith("_version_id") or key in {"visual_material", "attachments"}:
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
    if candidates.get("industry_slug") == "decoration":
        for section in ("title_formulas", "content_formulas"):
            for item in candidates[section]:
                source = item.get("source_content")
                if isinstance(source, dict):
                    source.pop("cross_industry", None)
    if mode == "original":
        view.pop("reference_candidates", None)
        candidates["scoring"].pop("reference", None)
    else:
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
