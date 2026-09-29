from __future__ import annotations

import json
import uuid
from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.agents.skills.repository import SkillRepository
from yuxi.content.control.industry.pack import (
    EvaluateIndustryPackRegressionHandler,
    ValidateIndustryPackHandler,
)
from yuxi.content.generation import SKILL_VERSIONS, refine_generated_content, review_generated_content
from yuxi.content.model.industry.pack import CONTENT_TYPE_CODES, IndustryPackPolicy
from yuxi.content.model.workflows.definition import WorkflowCatalog, WorkflowDefinitionPolicy, workflow_definition_hash
from yuxi.content.rules import CONTENT_GOALS
from yuxi.content.service_entry_form import (
    BRAND_NAME,
    CONTENT_TYPE_NAME_TO_DIRECTION,
    DECORATION_QUOTE_KEYS,
    LEGACY_QUOTE_TYPE_NAME,
    catalog_select_options,
    configured_business_variable_fields,
    content_direction_from_form_values,
    is_managed_content_type_name,
    is_quote_content_type_name,
)
from yuxi.services.business_variable_service import list_business_variables
from yuxi.services.content_type_service import list_content_types
from yuxi.services.process_standard_service import (
    list_enabled_process_names_by_type,
    list_enabled_process_type_names,
)
from yuxi.services.resident_population_service import list_enabled_resident_population_names
from yuxi.services.target_audience_service import list_enabled_target_audience_names
from yuxi.services.variable_service import SERVICE_ENTRIES, list_variables
from yuxi.content.v3.modular_rules import SINGLE_BLUEPRINT_WORKFLOW_IDS
from yuxi.content.schemas import (
    ChannelPreviewRequest,
    ContentArtifactAIEdit,
    ContentArtifactRegenerate,
    ContentArtifactUpdate,
    ContentBriefPayload,
    ContentRunCreate,
    ContentRunResume,
    ContentTaskCreate,
    ContentTaskUpdate,
    IndustryPackRegressionSubmission,
    IndustryPackTransitionRequest,
    MaterialConfirmation,
    MaterialCreate,
    RuleBundleUpdate,
    RuleDraftCreate,
)
from yuxi.content.v3.seed import PLATFORM_RULE_V3_ID
from yuxi.content.v3.workflow import LEGACY_PLATFORM_WORKFLOW_V3_IDS
from yuxi.content.validation import ComplianceEngine
from yuxi.content.validators import normalize_manual_evidence, validate_content
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.repositories.content_repository import ContentRepository
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.run_queue_service import get_arq_pool, list_run_stream_events
from yuxi.storage.postgres.models_business import AgentRun, User
from yuxi.storage.postgres.models_content import ContentArtifactVersion, ContentTask
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


def _content_error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "retryable": bool(extra.pop("retryable", False)),
                **extra,
            }
        },
    )


def _require_v3_task(task: ContentTask | None) -> None:
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    schema_version = int((task.runtime_config_snapshot_json or {}).get("schema_version") or 1)
    if schema_version != 3:
        raise _content_error(
            409,
            "CONTENT_LEGACY_TASK_READ_ONLY",
            "该任务由旧版内容工作流创建，仅保留历史查询；请新建 V3 任务继续生产",
            schema_version=schema_version,
        )
    if (task.runtime_config_snapshot_json or {}).get("creation_mode") != "viral_rewrite":
        raise _content_error(
            409,
            "CONTENT_CREATION_MODE_UNSUPPORTED",
            "原创任务已停止运行和编辑，仅保留历史查询",
        )


def _require_runnable_v3_task(task: ContentTask | None) -> None:
    _require_v3_task(task)
    if getattr(task, "workflow_version_id", None) in LEGACY_PLATFORM_WORKFLOW_V3_IDS:
        raise _content_error(
            409,
            "CONTENT_WORKFLOW_UPGRADE_REQUIRED",
            "该任务绑定旧版 V3 工作流与 checkpoint，仅保留历史查询；请复制或新建任务后使用新版工作流生产",
            workflow_version_id=task.workflow_version_id,
        )


def _validate_model_spec(model_spec: str | None) -> str | None:
    normalized = model_spec.strip() if isinstance(model_spec, str) else None
    if not normalized:
        return None
    info = model_cache.get_model_info(normalized)
    if not info or info.model_type != "chat":
        raise _content_error(422, "CONTENT_MODEL_UNAVAILABLE", f"未找到可用聊天模型：{normalized}")
    return normalized


def _clean_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _scoped_content_type_codes(bundle: dict, industry_slug: str, content_goal: str) -> set[str]:
    return {
        code
        for item in bundle.get("combination_rules") or []
        if item.get("enabled", True)
        and (not item.get("industry_scope") or industry_slug in item["industry_scope"])
        and (not item.get("content_goal_codes") or content_goal in item["content_goal_codes"])
        for code in item.get("content_type_codes") or []
    }


def normalize_rule_bundle(payload: RuleBundleUpdate) -> dict[str, Any]:
    bundle = payload.model_dump()
    bundle["changelog"] = bundle["changelog"].strip()
    list_fields = {
        "methods": ("industry_scope", "suitable_scenes", "sentence_patterns", "variable_schema", "risk_rules"),
        "title_formulas": (
            "industry_scope",
            "suitable_scenes",
            "reference_examples",
            "variable_schema",
            "compatible_methods",
            "risk_rules",
        ),
        "content_formulas": (
            "industry_scope",
            "compatible_methods",
            "suitable_scenes",
            "business_pains",
            "structure_schema",
            "reference_examples",
            "required_variables",
            "risk_rules",
        ),
        "combination_rules": (
            "content_type_codes",
            "industry_scope",
            "channel_scope",
            "narrative_axis_codes",
            "required_evidence_types",
            "content_goal_codes",
            "required_variable_codes",
            "title_formula_candidate_codes",
            "body_formula_candidate_codes",
        ),
    }
    for section, fields in list_fields.items():
        for index, item in enumerate(bundle[section]):
            for field in fields:
                item[field] = _clean_list(item[field])
            if "code" in item:
                item["code"] = item["code"].strip().upper()
            if section == "combination_rules":
                if int(item.get("schema_version") or 0) != 3:
                    raise _content_error(422, "CONTENT_RULES_V3_REQUIRED", "只能编辑 V3 组合规则")
                item["method_members"] = [
                    {
                        **member,
                        "method_code": str(member.get("method_code") or "").strip().upper(),
                    }
                    for member in item.get("method_members") or []
                ]
                for field in (
                    "content_type_codes",
                    "title_formula_candidate_codes",
                    "body_formula_candidate_codes",
                ):
                    item[field] = [code.upper() for code in item[field]]
            elif "compatible_methods" in item:
                item["compatible_methods"] = [code.upper() for code in item["compatible_methods"]]
            item["sort_order"] = index

    for section in ("methods", "title_formulas", "content_formulas"):
        codes = [item["code"] for item in bundle[section]]
        duplicate_codes = sorted({code for code in codes if codes.count(code) > 1})
        if duplicate_codes:
            raise _content_error(
                422,
                "CONTENT_RULE_CODE_DUPLICATED",
                f"{section} 存在重复编码：{', '.join(duplicate_codes)}",
                section=section,
                codes=duplicate_codes,
            )
    return bundle


def validate_rule_bundle_for_publish(bundle: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    methods = {item["code"]: item for item in bundle.get("methods") or []}
    titles = {item["code"]: item for item in bundle.get("title_formulas") or []}
    bodies = {item["code"]: item for item in bundle.get("content_formulas") or []}
    combination_rules = bundle.get("combination_rules") or []

    def add_error(code: str, message: str, path: str) -> None:
        errors.append({"code": code, "message": message, "path": path})

    for section in ("title_formulas", "content_formulas"):
        for index, formula in enumerate(bundle.get(section) or []):
            application = (formula.get("source_content") or {}).get("cross_industry")
            if application is None:
                continue
            if not all(
                isinstance(application.get(key), str) and application[key].strip() for key in ("name", "core_goal")
            ):
                add_error("CROSS_INDUSTRY_FORMULA_INVALID", "通用公式名称与核心目标不能为空", f"{section}.{index}")
            if section == "content_formulas" and not (
                isinstance(application.get("structure_schema"), list)
                and application["structure_schema"]
                and all(isinstance(line, str) and line.strip() for line in application["structure_schema"])
            ):
                add_error("CROSS_INDUSTRY_FORMULA_INVALID", "通用正文结构不能为空", f"{section}.{index}")

    if not combination_rules or any(int(item.get("schema_version") or 0) != 3 for item in combination_rules):
        add_error(
            "V3_COMBINATION_RULES_REQUIRED",
            "规则版本必须只包含 V3 组合组",
            "combination_rules",
        )
        return {"errors": errors, "warnings": warnings}

    valid_content_types = {item["code"] for item in bundle.get("content_types") or [] if item.get("enabled", True)}
    deterministic_decoration_rules = any(
        item.get("enabled", True)
        and (not item.get("industry_scope") or "decoration" in item["industry_scope"])
        and (item.get("source_metadata") or {}).get("composition_blueprint")
        for item in combination_rules
    )
    enabled_direction_rules: dict[str, list[int]] = {code: [] for code in valid_content_types}
    for index, item in enumerate(combination_rules):
        path = f"combination_rules.{index}"
        if item.get("enabled", True) and (not item.get("industry_scope") or "decoration" in item["industry_scope"]):
            for code in item.get("content_type_codes") or []:
                if code in enabled_direction_rules:
                    enabled_direction_rules[code].append(index)
        if item.get("enabled", True) and (
            not any(
                titles.get(code, {}).get("enabled", True) for code in item.get("title_formula_candidate_codes") or []
            )
            or not any(
                bodies.get(code, {}).get("enabled", True) for code in item.get("body_formula_candidate_codes") or []
            )
        ):
            warnings.append(
                {
                    "code": "V3_NO_ENABLED_FORMULA",
                    "message": "该组合的可用公式已全部停用，将不参与新任务匹配",
                    "path": path,
                }
            )
        members = [member for member in item.get("method_members") or [] if isinstance(member, dict)]
        member_codes = {member.get("method_code") for member in members}
        unknown_methods = member_codes - set(methods)
        unknown_titles = set(item.get("title_formula_candidate_codes") or []) - set(titles)
        unknown_bodies = set(item.get("body_formula_candidate_codes") or []) - set(bodies)
        unknown_types = set(item.get("content_type_codes") or []) - valid_content_types
        if item.get("combination_type") not in {"single", "double", "triple", "quadruple"}:
            add_error("V3_COMBINATION_TYPE_INVALID", "V3 组合类型无效", f"{path}.combination_type")
        expected_size = {"single": 1, "double": 2, "triple": 3, "quadruple": 4}.get(item.get("combination_type"))
        if expected_size is not None and len(members) != expected_size:
            add_error(
                "V3_COMBINATION_SIZE_INVALID",
                "V3 组合类型与创作手法数量不一致",
                f"{path}.method_members",
            )
        if [member.get("order") for member in members] != list(range(1, len(members) + 1)):
            add_error(
                "V3_METHOD_ORDER_INVALID",
                "V3 创作手法顺序必须从 1 连续递增",
                f"{path}.method_members",
            )
        if len(member_codes) != len(members):
            add_error(
                "V3_METHOD_MEMBERS_DUPLICATED",
                "V3 组合组不能重复引用同一创作手法",
                f"{path}.method_members",
            )
        if not member_codes:
            add_error("V3_METHOD_MEMBERS_REQUIRED", "V3 组合组至少包含一个创作手法", f"{path}.method_members")
        elif unknown_methods:
            add_error(
                "V3_METHOD_MEMBERS_INVALID",
                f"V3 组合组引用了未知手法：{', '.join(sorted(unknown_methods))}",
                f"{path}.method_members",
            )
        if unknown_types or not item.get("content_type_codes"):
            add_error("V3_CONTENT_TYPE_INVALID", "V3 组合组内容方向无效", f"{path}.content_type_codes")
        if not item.get("title_formula_candidate_codes") or unknown_titles:
            add_error(
                "V3_TITLE_POOL_INVALID",
                "V3 标题公式候选池为空或引用无效",
                f"{path}.title_formula_candidate_codes",
            )
        if not item.get("body_formula_candidate_codes") or unknown_bodies:
            add_error(
                "V3_BODY_POOL_INVALID",
                "V3 正文公式候选池为空或引用无效",
                f"{path}.body_formula_candidate_codes",
            )
        elif (
            deterministic_decoration_rules
            and item.get("enabled", True)
            and (not item.get("industry_scope") or "decoration" in item["industry_scope"])
            and len(item["body_formula_candidate_codes"]) != 1
            and not (
                item.get("content_type_codes") in (["CT06"], ["CT07"])
                and (item.get("source_metadata") or {}).get("formula_selection_policy") == "evidence_composition_v1"
            )
        ):
            add_error(
                "DETERMINISTIC_BODY_FORMULA_REQUIRED",
                "装修确定性计划的组合规则必须只绑定一个正文公式",
                f"{path}.body_formula_candidate_codes",
            )
        if (
            deterministic_decoration_rules
            and item.get("enabled", True)
            and (not item.get("industry_scope") or "decoration" in item["industry_scope"])
        ):
            blueprint = (item.get("source_metadata") or {}).get("composition_blueprint") or {}
            layers = blueprint.get("layer_sequence") or []
            phrase_rules = blueprint.get("phrase_composition") or []
            layer_codes = [layer.get("code") for layer in layers if isinstance(layer, dict)]
            if (
                not layers
                or len(layer_codes) != len(layers)
                or len(layer_codes) != len(set(layer_codes))
                or [layer.get("order") for layer in layers] != list(range(1, len(layers) + 1))
            ):
                add_error(
                    "DETERMINISTIC_BLUEPRINT_LAYERS_INVALID",
                    "装修组合规则必须配置编码唯一、顺序连续的层级",
                    f"{path}.source_metadata.composition_blueprint.layer_sequence",
                )
            for phrase_index, phrase_rule in enumerate(phrase_rules):
                selection = phrase_rule.get("selection")
                minimum = phrase_rule.get("min_groups")
                maximum = phrase_rule.get("max_groups")
                missing_behavior = phrase_rule.get("missing_behavior", "block")
                if (
                    phrase_rule.get("layer_code") not in layer_codes
                    or selection not in {"fixed", "all", "random", "available"}
                    or not isinstance(minimum, int)
                    or minimum < 0
                    or (maximum is not None and (not isinstance(maximum, int) or maximum < minimum))
                    or missing_behavior not in {"block", "omit", "ask_user"}
                ):
                    add_error(
                        "DETERMINISTIC_BLUEPRINT_PHRASE_INVALID",
                        "词组组合的层级、选择方式、组数或缺失处理无效",
                        f"{path}.source_metadata.composition_blueprint.phrase_composition.{phrase_index}",
                    )
    for code, indexes in sorted(enabled_direction_rules.items() if deterministic_decoration_rules else []):
        if len(indexes) != 1:
            add_error(
                "DETERMINISTIC_DIRECTION_RULE_UNIQUE",
                f"装修创作类型 {code} 必须且只能有一条已启用组合规则，当前为 {len(indexes)} 条",
                "combination_rules",
            )
    return {"errors": errors, "warnings": warnings}


def _task_name(template_name: str, content_goal: str) -> str:
    goal_name = next((item["name"] for item in CONTENT_GOALS if item["code"] == content_goal), content_goal)
    return f"{template_name} · {goal_name}"


def _brief_field_value(brief: dict[str, Any], key: str) -> Any:
    # 发布渠道由任务绑定，旧表单中的空值不能覆盖已解析的渠道版本。
    if key == "channel_profile_version_id":
        return brief.get(key)
    form_values = brief.get("form_values") or {}
    if key in form_values:
        return form_values[key]
    if key == "brand_name":
        return (brief.get("brand") or {}).get("name")
    if key == "audience":
        return brief.get("audience")
    if key in {"required_terms", "forbidden_terms"}:
        return brief.get(key)
    if key in {"persona_profile_version_id", "attachments"}:
        return brief.get(key)
    return (brief.get("business_variables") or {}).get(key)


def _parse_content_studio_quote_case(
    user_request: str,
    *,
    content_type_code: str | None,
) -> dict[str, Any] | None:
    """Convert a standardized Dangjia quote JSON into production facts and a trusted snapshot."""
    try:
        raw_payload = json.loads(user_request)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw_payload, dict):
        return None
    requirement = raw_payload.get("requirementType")
    type_name = str(requirement.get("typeName") or "").strip() if isinstance(requirement, dict) else ""
    if not is_quote_content_type_name(type_name):
        return None
    prices = requirement.get("prices") if isinstance(requirement, dict) else None
    quotation = requirement.get("quotationInfo") if isinstance(requirement, dict) else None
    if type_name != LEGACY_QUOTE_TYPE_NAME and not (
        isinstance(prices, list) and prices and isinstance(quotation, dict)
    ):
        return None

    # Local import avoids a module cycle: Dangjia's HTTP orchestration calls this service.
    from yuxi.services.dangjia_service import (
        TRUSTED_QUOTE_SNAPSHOT_KEY,
        DangjiaContentCreate,
        _resolve_ct_code,
        build_dangjia_form_values,
        build_persona_description,
        build_trusted_quote_snapshot,
    )

    try:
        payload = DangjiaContentCreate.model_validate(raw_payload)
    except ValidationError as exc:
        first_error = exc.errors(include_url=False)[0]
        path = ".".join(str(item) for item in first_error.get("loc") or ())
        raise _content_error(
            422,
            "CONTENT_QUOTE_CASE_SCHEMA_INVALID",
            f"施工报价案例不符合标准数据结构：{path or 'payload'} {first_error.get('msg') or ''}".strip(),
        ) from exc
    try:
        resolved_content_type_code = _resolve_ct_code(payload.requirementType)
    except HTTPException as exc:
        error = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
        raise _content_error(
            exc.status_code,
            str(error.get("code") or "CONTENT_QUOTE_CASE_INVALID"),
            str(error.get("message") or "施工报价案例数据无效"),
        ) from exc
    if content_type_code != resolved_content_type_code:
        raise _content_error(
            422,
            "CONTENT_QUOTE_TYPE_MISMATCH",
            f"当前任务创作类型为 {content_type_code or '未选择'}，报价案例要求 {resolved_content_type_code}",
            selected_content_type_code=content_type_code,
            required_content_type_code=resolved_content_type_code,
        )

    form_values = build_dangjia_form_values(payload)
    persona_fact = build_persona_description(payload.persona)
    product = str(form_values.get("project_type") or "").strip()
    quantity = str(form_values.get("area") or "").strip()
    process = str(form_values.get("craft_and_materials") or "").strip()
    location = str(form_values.get("location") or form_values.get("project_site") or "").strip()
    quote_format = str(form_values.get("quote_format") or "").strip()
    tags = [str(item).strip() for item in form_values.get("content_tags") or [] if str(item).strip()]
    positioning = (
        "旧房改造" if any(token in tag for tag in tags for token in ("旧房", "老房", "二手房")) else "同城装修"
    )
    canonical_house_type = next(
        (
            canonical
            for aliases, canonical in (
                (("一室", "一居", "单间"), "一居室"),
                (("二室", "两室", "二居", "两居"), "两居室"),
                (("三室", "三居"), "大三房"),
            )
            if any(alias in product for alias in aliases)
        ),
        product,
    )
    scene = " ".join(dict.fromkeys(item for item in (positioning, canonical_house_type, product) if item))
    sanitized_user_request = (
        f"{location + '，' if location else ''}{quantity}{product}，{quote_format}施工报价；"
        "标题价格与报价明细已作为锁定事实保存。"
    )
    business_variables = {
        "external_serial_no": form_values["external_serial_no"],
        "external_source": "content_studio_case",
        "location": location,
        "scene": scene,
        "product": product,
        "quantity": quantity,
        "process": process,
        "pain": form_values["pain"],
        "advantages": form_values["advantage"],
        "persona_fact": persona_fact,
        "project_site": form_values["project_site"],
        "content_tags": form_values["content_tags"],
        "quote_format": quote_format,
        "type_name": form_values["type_name"],
    }
    business_variables = {key: value for key, value in business_variables.items() if value not in (None, "", [], {})}
    trusted_snapshot = build_trusted_quote_snapshot(
        payload,
        content_type_code=resolved_content_type_code,
    )
    if trusted_snapshot is None:
        raise _content_error(422, "CONTENT_QUOTE_CASE_INVALID", "施工报价案例缺少可信报价快照")
    trusted_snapshot.update(
        {
            "ingestion_channel": "content_studio_case",
            "sanitized_user_request": sanitized_user_request,
        }
    )
    return {
        "sanitized_user_request": sanitized_user_request,
        "business_variables": business_variables,
        "brand": {"name": form_values["brand_name"]},
        "audience": form_values["audience"],
        "persona": {
            "description": persona_fact,
            "tone": payload.persona.tone,
            "structured": payload.persona.model_dump(mode="json"),
        },
        "trusted_snapshot_key": TRUSTED_QUOTE_SNAPSHOT_KEY,
        "trusted_snapshot": trusted_snapshot,
    }


def _studio_content_type_name(raw_payload: dict[str, Any]) -> str:
    content_type = raw_payload.get("contentType")
    if isinstance(content_type, dict):
        name = str(content_type.get("typeName") or "").strip()
        if name:
            return name
    requirement = raw_payload.get("requirementType")
    if isinstance(requirement, dict):
        return str(requirement.get("typeName") or "").strip()
    return ""


def _studio_location_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            str(value.get(key) or "").strip()
            for key in ("province", "city", "district")
            if str(value.get(key) or "").strip()
        )
    if isinstance(value, list):
        return " ".join(part for item in value if (part := _studio_location_text(item)))
    return str(value or "").strip()


def _resolve_studio_location(*sources: dict[str, Any] | None) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("location", "所在区域", "楼盘信息", "project_site", "community_name"):
            text = _studio_location_text(source.get(key))
            if text:
                return text
    return ""


def _parse_content_studio_production_pack(user_request: str) -> dict[str, Any] | None:
    """把任务页生产前资料包中的 facts 提升为工厂变量。"""
    try:
        raw_payload = json.loads(user_request)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw_payload, dict):
        return None
    type_name = _studio_content_type_name(raw_payload)
    if not is_managed_content_type_name(type_name):
        return None
    facts = raw_payload.get("facts")
    if not isinstance(facts, dict):
        return None
    variables = {
        str(key).strip(): value for key, value in facts.items() if str(key).strip() and value not in (None, "", [], {})
    }
    if not variables:
        return None
    if variables.get("advantage") and not variables.get("advantages"):
        variables["advantages"] = variables["advantage"]
    if variables.get("pain") and not variables.get("pain_points"):
        variables["pain_points"] = variables["pain"]
    location = _resolve_studio_location(variables, raw_payload.get("businessVariables"))
    if location:
        variables["location"] = location
    business_variables = raw_payload.get("businessVariables")
    community = ""
    for source in (variables, business_variables if isinstance(business_variables, dict) else {}):
        community = str(
            source.get("case_background")
            or source.get("楼盘信息")
            or source.get("community_name")
            or source.get("project_site")
            or ""
        ).strip()
        if community:
            break
    if community and not variables.get("case_background"):
        variables["case_background"] = community
    content_type = raw_payload.get("contentType") if isinstance(raw_payload.get("contentType"), dict) else {}
    declared_code = str(content_type.get("contentTypeCode") or "").strip()
    mapped_code = CONTENT_TYPE_NAME_TO_DIRECTION.get(type_name)
    # 托管内容类型名称优先：工艺施工展示必须走 CT06，不能被历史客户端传的 CT05（施工报价）带走。
    content_type_code = mapped_code or declared_code or None
    audience = variables.get("audience")
    if isinstance(audience, str) and audience.strip():
        audience = [audience.strip()]
    if not isinstance(audience, list):
        audience = []
    audience = [str(item).strip() for item in audience if str(item).strip()]
    if content_type_code == "CT06" and not variables.get("craft_role") and audience:
        variables["craft_role"] = audience
    persona_fact = str(variables.get("persona_fact") or "").strip()
    return {
        "type_name": type_name,
        "content_type_code": content_type_code,
        "business_variables": variables,
        "brand": {"name": BRAND_NAME},
        "audience": audience,
        "persona": {"description": persona_fact} if persona_fact else {},
    }


def _parse_content_studio_persona_case(user_request: str, *, content_type_code: str | None) -> dict[str, Any]:
    """保留自我介绍、工艺展示、日常记录结构化原文中的作者事实，不推断用户痛点或工作成果。"""
    if content_type_code not in {"CT01", "CT06", "CT07"}:
        return {}
    try:
        payload = json.loads(user_request)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("persona"), dict):
        return {}
    requirement = payload.get("requirementType")
    if not isinstance(requirement, dict) or requirement.get("typeName") not in {
        "自我介绍",
        "日常",
        "日常工作",
        "工艺展示",
        "施工工艺",
    }:
        return {}

    from yuxi.services.dangjia_service import DangjiaPersona, build_persona_description

    try:
        persona = DangjiaPersona.model_validate(payload["persona"])
    except ValidationError as exc:
        raise _content_error(422, "CONTENT_PERSONA_CASE_INVALID", "结构化案例的人设字段不符合数据结构") from exc
    tags = payload.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise _content_error(422, "CONTENT_PERSONA_CASE_INVALID", "结构化案例 tags 必须为字符串数组")
    house_info = requirement.get("houseInfo") if isinstance(requirement.get("houseInfo"), dict) else {}
    values = {
        "location": (persona.serviceCity or "").strip(),
        "persona_fact": build_persona_description(persona),
        "product": "、".join(item.strip() for item in persona.skills if item.strip()),
        "advantages": [item.strip() for item in persona.serviceAdvantages if item.strip()],
        "project_site": house_info.get("mySite"),
        "content_tags": tags,
    }
    if content_type_code in {"CT06", "CT07"}:
        process_tags = [
            tag.strip()
            for tag in tags
            if tag.strip() and tag.strip() not in {"工艺展示", "施工工艺", "日常", "日常工作"}
        ]
        values["process"] = process_tags
        values["case_background"] = house_info.get("mySite")
        values["project"] = "、".join(process_tags)
        # 身份可作为标题说话人；会水电/泥瓦不代表本次拆除对象就是水电/泥瓦。
        values["craft_role"] = [
            skill.strip() for skill in persona.skills if skill.strip() in {"工长", "设计师", "项目经理"}
        ]
        inspections = [tag for tag in process_tags if "巡检" in tag or "巡查" in tag]
        if inspections:
            values["inspection"] = "、".join(inspections)
        kickoffs = [tag for tag in process_tags if "开工" in tag]
        if kickoffs:
            values["kickoff"] = "、".join(kickoffs)
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def _separate_app_persona_facts(compiled: dict, task) -> None:
    """新工作流不把 App 的能力聚合字段当作施工过程；原始结构化资料仍保留。"""
    if getattr(task, "workflow_version_id", None) not in SINGLE_BLUEPRINT_WORKFLOW_IDS:
        return
    persona = compiled.get("persona") or {}
    structured = persona.get("structured")
    if not structured:
        return
    compiled["fact_source_paths"] = {
        "persona_fact": "persona",
        "capability_description": "persona.skills,persona.serviceAdvantages",
        "advantages": "persona.serviceAdvantages",
        "advantage": "persona.serviceAdvantages",
        "location": "persona.serviceCity,requirementType.mySite",
        "quantity": "requirementType.quotationInfo.houseArea",
        "area": "requirementType.quotationInfo.houseArea",
        "product": "requirementType.quotationInfo.houseType",
        "project_type": "requirementType.quotationInfo.houseType",
        "project_site": "requirementType.mySite",
        "case_background": "requirementType.mySite",
    }
    from yuxi.services.dangjia_service import DangjiaPersona, build_persona_description

    fact_persona = DangjiaPersona.model_validate({**structured, "tone": ""})
    persona["description"] = build_persona_description(fact_persona)
    variables = compiled["business_variables"]
    variables["persona_fact"] = persona["description"]
    if variables.get("external_source") in {"dangjia", "content_studio_case"}:
        # App 适配器为旧表单补出的痛点不是接口资料，也不应规定新作者的文章主题。
        for source in (variables, compiled["form_values"]):
            source.pop("pain", None)
            source.pop("pain_points", None)
    if variables.get("project_site"):
        # App 的项目背景沿用行业包已发布变量，不另建无治理的自由事实入口。
        variables.setdefault("case_background", variables["project_site"])
    if variables.get("scene") and variables.get("product"):
        tags = variables.get("content_tags") or []
        positioning = (
            "旧房改造" if any(token in tag for tag in tags for token in ("旧房", "老房", "二手房")) else "同城装修"
        )
        variables["scene"] = f"{positioning}，{variables['product']}"
    for source in (variables, compiled["form_values"]):
        for key in ("process", "craft_and_materials"):
            value = source.get(key)
            if isinstance(value, str) and value.startswith(("工种能力：", "服务优势：")):
                source.pop(key)


def compile_content_brief(
    *, task: ContentTask, template: Any, brief: ContentBriefPayload
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw = brief.model_dump()
    user_request = str(raw.get("user_request") or (raw.get("form_values") or {}).get("user_request") or "").strip()
    content_type_code = getattr(task, "content_type_code", None)
    if user_request:
        quote_case = _parse_content_studio_quote_case(user_request, content_type_code=content_type_code)
        production_pack = None if quote_case else _parse_content_studio_production_pack(user_request)
        structured = quote_case or production_pack
        existing_snapshot = (getattr(task, "runtime_config_snapshot_json", None) or {}).get(
            "trusted_external_material_snapshot"
        )
        if (
            quote_case is None
            and isinstance(existing_snapshot, dict)
            and existing_snapshot.get("ingestion_channel") == "content_studio_case"
            and existing_snapshot.get("sanitized_user_request") == user_request
            and getattr(task, "brief_json", None)
        ):
            compiled = deepcopy(task.brief_json)
            compiled["visual_material"] = raw.get("visual_material")
            return compiled, []
        quote_type = {
            "CT02": "standard_unit_price",
            "CT03": "standard_unit_price",
            "CT04": "project_quote",
            "CT05": "project_quote",
        }.get(content_type_code)
        normalized_user_request = quote_case["sanitized_user_request"] if quote_case is not None else user_request
        normalized_variables = (
            quote_case["business_variables"]
            if quote_case is not None
            else _parse_content_studio_persona_case(user_request, content_type_code=content_type_code)
        )
        persona = (
            quote_case["persona"]
            if quote_case is not None
            else dict((production_pack or {}).get("persona") or {})
        )
        if (
            quote_case is None
            and normalized_variables.get("persona_fact")
            and getattr(task, "workflow_version_id", None) in SINGLE_BLUEPRINT_WORKFLOW_IDS
        ):
            # 上面的结构化案例解析已完成验证；非报价类型同样保留语气与事实的边界。
            structured = json.loads(user_request)["persona"]
            persona = {
                "description": normalized_variables["persona_fact"],
                "tone": structured.get("tone"),
                "structured": structured,
            }
        normalized_user_request = quote_case["sanitized_user_request"] if quote_case is not None else user_request
        normalized_variables = dict((structured or {}).get("business_variables") or {})
        form_values = dict(raw.get("form_values") or {})
        form_values["user_request"] = normalized_user_request
        reserved = {
            "brand_name",
            "audience",
            "persona",
            "required_terms",
            "forbidden_terms",
            "knowledge_scope",
            "user_request",
        }
        form_variables = {
            key: value for key, value in form_values.items() if key not in reserved and value not in (None, "", [])
        }
        resolved_type_code = (
            (structured or {}).get("content_type_code")
            or content_direction_from_form_values(form_values)
            or raw.get("content_type_code")
            or content_type_code
        )
        inject_quote_type = bool(quote_type) and quote_case is None and production_pack is None
        if production_pack is not None and is_quote_content_type_name(production_pack.get("type_name")):
            normalized_variables.setdefault("quote_type", "budget")
            if not normalized_variables.get("price"):
                price_items = [
                    f"{key} {form_values[key]}".strip()
                    for key in DECORATION_QUOTE_KEYS
                    if form_values.get(key) not in (None, "", [])
                ]
                if price_items:
                    normalized_variables["price"] = price_items
        if quote_case is None and production_pack is None:
            for key, value in _parse_content_studio_persona_case(
                user_request, content_type_code=content_type_code
            ).items():
                normalized_variables.setdefault(key, value)
        location = _resolve_studio_location(normalized_variables, form_values)
        if location:
            normalized_variables["location"] = location
        compiled = {
            "task_id": task.id,
            "industry": template.slug,
            "content_goal": task.content_goal,
            "content_type_code": resolved_type_code,
            "industry_pack_version_id": getattr(task, "industry_pack_version_id", None),
            "channel_profile_version_id": getattr(task, "channel_profile_version_id", None),
            "persona_profile_version_id": getattr(task, "persona_profile_version_id", None),
            "mode": task.mode,
            "brand": structured["brand"] if structured is not None else {},
            "audience": structured["audience"] if structured is not None else [],
            "business_variables": {
                "user_request": normalized_user_request,
                **({"quote_type": quote_type} if inject_quote_type else {}),
                **normalized_variables,
                **form_variables,
            },
            "persona": persona,
            "required_terms": list(raw.get("required_terms") or []),
            "forbidden_terms": list(raw.get("forbidden_terms") or []),
            "attachments": [],
            "locked_fields": [],
            **(
                {"original_user_request": user_request}
                if getattr(task, "workflow_version_id", None) in SINGLE_BLUEPRINT_WORKFLOW_IDS
                else {}
            ),
            "user_request": normalized_user_request,
            "form_values": form_values,
            "material_confirmations": [],
            "visual_material": raw.get("visual_material"),
        }
        _separate_app_persona_facts(compiled, task)
        return compiled, []

    form_values = dict(raw.get("form_values") or {})
    business_variables = dict(raw.get("business_variables") or {})
    # knowledge_scope 仅用于忽略旧任务表单遗留值；知识库范围由 Agent 管理配置决定。
    reserved = {"brand_name", "audience", "persona", "required_terms", "forbidden_terms", "knowledge_scope"}
    business_variables.update(
        {key: value for key, value in form_values.items() if key not in reserved and value not in (None, "", [])}
    )
    fields = template.quick_form_schema if task.mode == "quick" else template.pro_form_schema
    # 行业表单只负责行业语言，V3 生成协议消费稳定的平台变量。字段映射来自
    # 已发布表单/行业包配置，新增行业无需修改 Skill 或工作流代码。
    for field in fields or []:
        variable_code = field.get("variable_code")
        value = form_values.get(field.get("key"))
        if variable_code and value not in (None, "", []):
            business_variables[variable_code] = value
    if business_variables.get("pain") and not business_variables.get("pain_points"):
        business_variables["pain_points"] = business_variables["pain"]
    if business_variables.get("advantage") and not business_variables.get("advantages"):
        business_variables["advantages"] = business_variables["advantage"]
    quote_type_by_content_type = {
        "CT02": "standard_unit_price",
        "CT03": "standard_unit_price",
        "CT04": "project_quote",
        "CT05": "project_quote",
    }
    if content_type_code in quote_type_by_content_type:
        business_variables.setdefault("quote_type", quote_type_by_content_type[content_type_code])
    brand = dict(raw.get("brand") or {})
    if form_values.get("brand_name"):
        brand["name"] = form_values["brand_name"]
    audience = raw.get("audience") or form_values.get("audience") or []
    if isinstance(audience, str):
        audience = [item.strip() for item in audience.split(",") if item.strip()]
    persona = dict(raw.get("persona") or {})
    if form_values.get("persona"):
        persona.setdefault("description", form_values["persona"])

    compiled = {
        "task_id": task.id,
        "industry": template.slug,
        "content_goal": task.content_goal,
        "content_type_code": (
            content_direction_from_form_values(form_values)
            or raw.get("content_type_code")
            or getattr(task, "content_type_code", None)
        ),
        "industry_pack_version_id": getattr(task, "industry_pack_version_id", None),
        "channel_profile_version_id": getattr(task, "channel_profile_version_id", None),
        "persona_profile_version_id": getattr(task, "persona_profile_version_id", None),
        "mode": task.mode,
        "brand": brand,
        "audience": audience,
        "business_variables": business_variables,
        "persona": persona,
        "required_terms": raw.get("required_terms") or form_values.get("required_terms") or [],
        "forbidden_terms": raw.get("forbidden_terms") or form_values.get("forbidden_terms") or [],
        "attachments": raw.get("attachments") or [],
        "locked_fields": raw.get("locked_fields") or [],
        "user_request": "",
        "form_values": form_values,
        "material_confirmations": raw.get("material_confirmations") or [],
        "visual_material": raw.get("visual_material"),
    }
    missing = []
    # 单输入框提交为空时只提示当前可见字段，不能回到旧行业表单校验。
    if "user_request" in brief.model_fields_set or "user_request" in form_values:
        return compiled, [{"field": "user_request", "label": "内容需求"}]
    for field in fields or []:
        if not field.get("required"):
            continue
        value = _brief_field_value(compiled, field["key"])
        if value in (None, "", []):
            missing.append({"field": field["key"], "label": field.get("label") or field["key"]})
    _separate_app_persona_facts(compiled, task)
    return compiled, missing


async def get_content_bootstrap(db: AsyncSession, user: User) -> dict[str, Any]:
    from yuxi.content.model.strategy import load_selection_policy

    repo = ContentRepository(db)
    version = await repo.get_published_rule_version(schema_version=3)
    if version is None:
        raise _content_error(503, "CONTENT_RULES_NOT_INITIALIZED", "创作规则库尚未初始化")
    rule_bundle = await repo.get_rule_bundle(version.id)
    policy = load_selection_policy()
    templates = await repo.list_templates()
    from yuxi.content.v3.joint_workflow import BLUEPRINT_FIRST_WORKFLOW_IDS

    for template in templates:
        template["strategy_mode"] = policy["industry_modes"].get(template["slug"], policy["default_mode"])
        from yuxi.content.v3.joint_workflow import (
            PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID,
            PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID,
        )

        # single-blueprint 与 standardized-factory 同属自动选方向入口，前端不得再强制一级内容方向。
        template["blueprint_first"] = template["default_workflow_version_id"] in {
            *BLUEPRINT_FIRST_WORKFLOW_IDS,
            PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID,
            PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID,
            *SINGLE_BLUEPRINT_WORKFLOW_IDS,
        }
    return {
        "industry_templates": templates,
        "content_goals": CONTENT_GOALS,
        "content_types": (rule_bundle or {}).get("content_types") or [],
        "content_variables": (await list_variables(db))["variables"],
        "managed_content_types": (await list_content_types(db))["content_types"],
        "target_audiences": await list_enabled_target_audience_names(db),
        "resident_populations": await list_enabled_resident_population_names(db),
        "process_types": await list_enabled_process_type_names(db),
        "process_names_by_type": await list_enabled_process_names_by_type(db),
        "business_variable_bindings": (await list_business_variables(db))["business_variables"],
        "industry_packs": await repo.list_industry_packs(),
        "channel_profiles": await repo.list_channel_profiles(),
        "personas": await repo.list_personas(user),
        "rule_bundle": rule_bundle,
    }


def _media_evidence_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "task_id": item.task_id,
        "attachment_id": item.attachment_id,
        "object_uri": item.object_uri,
        "media_type": item.media_type,
        "original_filename": item.original_filename,
        "extracted_text": item.extracted_text,
        "metadata": item.metadata_json or {},
        "source_hash": item.source_hash,
        "verified_status": item.verified_status,
        "privacy_status": item.privacy_status,
        "allowed_usage": item.allowed_usage or [],
        "confirmed_facts": item.confirmed_facts or [],
        "confirmed_by": item.confirmed_by,
        "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


async def add_task_material(db: AsyncSession, user: User, task_id: str, payload: MaterialCreate) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_v3_task(task)
    try:
        item = await repo.create_media_evidence(task_id=task.id, **payload.model_dump())
        task.brief_json = {
            **(task.brief_json or {}),
            "attachments": [
                *[
                    attachment
                    for attachment in ((task.brief_json or {}).get("attachments") or [])
                    if attachment.get("attachment_id") != payload.attachment_id
                ],
                {
                    "media_evidence_id": item.id,
                    "attachment_id": item.attachment_id,
                    "media_type": item.media_type,
                    "original_filename": item.original_filename,
                    "verified_status": item.verified_status,
                    "privacy_status": item.privacy_status,
                },
            ],
        }
        await repo.track(
            "content_material_ingested",
            uid=str(user.uid),
            task_id=task.id,
            properties={"media_type": item.media_type, "attachment_id": item.attachment_id},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _content_error(409, "CONTENT_MATERIAL_DUPLICATED", "该附件已经加入当前任务")
    return {"material": _media_evidence_dict(item)}


async def list_task_materials(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    return {"items": [_media_evidence_dict(item) for item in await repo.list_media_evidence(task.id)]}


async def confirm_task_material(
    db: AsyncSession,
    user: User,
    task_id: str,
    evidence_id: str,
    payload: MaterialConfirmation,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_v3_task(task)
    item = await repo.get_media_evidence(evidence_id)
    if item is None or item.task_id != task.id:
        raise _content_error(404, "CONTENT_MATERIAL_NOT_FOUND", "任务素材不存在")
    item.verified_status = payload.verified_status
    item.privacy_status = payload.privacy_status
    item.allowed_usage = payload.allowed_usage
    item.confirmed_facts = payload.confirmed_facts
    item.confirmed_by = str(user.uid)
    item.confirmed_at = utc_now_naive()
    fact_values = {
        str(fact.get("variable_code") or fact.get("key")): fact.get("value")
        for fact in payload.confirmed_facts
        if (fact.get("variable_code") or fact.get("key")) and fact.get("value") not in (None, "", [])
    }
    evidence_items = [
        evidence for evidence in ((task.evidence_json or {}).get("items") or []) if evidence.get("id") != item.id
    ]
    if payload.verified_status == "confirmed" and payload.privacy_status == "approved":
        evidence_items.append(
            {
                "id": item.id,
                "type": "media_evidence",
                "source_type": "media",
                "source_id": item.attachment_id,
                "source_version": item.source_hash,
                "content": item.extracted_text,
                "values": fact_values,
                "variable_codes": sorted(fact_values),
                "verified_status": "confirmed",
                "privacy_status": "approved",
                "allowed_usage": payload.allowed_usage,
            }
        )
    task.evidence_json = {
        **(task.evidence_json or {}),
        "items": evidence_items,
        "summary": {
            **((task.evidence_json or {}).get("summary") or {}),
            "media": sum(1 for evidence in evidence_items if evidence.get("source_type") == "media"),
        },
    }
    await repo.track(
        "content_material_confirmed",
        uid=str(user.uid),
        task_id=task.id,
        properties={
            "media_evidence_id": item.id,
            "verified_status": item.verified_status,
            "privacy_status": item.privacy_status,
        },
    )
    await db.commit()
    return {"material": _media_evidence_dict(item)}


async def preview_task_channel(
    db: AsyncSession,
    user: User,
    task_id: str,
    payload: ChannelPreviewRequest,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    channels = await repo.list_channel_profiles()
    channel = next((item for item in channels if item["id"] == payload.channel_profile_version_id), None)
    if channel is None:
        raise _content_error(404, "CONTENT_CHANNEL_PROFILE_INVALID", "渠道配置不存在或未发布")
    template = await repo.get_template(task.industry_template_version_id)
    industry_slug = template.slug if template else None
    policies = [
        item
        for item in await repo.list_compliance_policies()
        if item["scope_type"] == "platform"
        or (item["scope_type"] == "channel" and item["scope_id"] == channel["code"])
        or (item["scope_type"] == "industry" and item["scope_id"] == industry_slug)
        or (item["scope_type"] == "enterprise" and item["tenant_id"] == task.tenant_id)
    ]
    result = ComplianceEngine().validate_and_adapt(
        title=payload.title,
        body=payload.body,
        topics=payload.topics,
        channel_profile=channel,
        policies=policies,
    )
    return {
        "channel": channel,
        "preview": result,
        "policy_version_ids": [item["id"] for item in policies],
    }


async def create_content_task(db: AsyncSession, user: User, payload: ContentTaskCreate) -> dict[str, Any]:
    from yuxi.content.model.strategy import load_selection_policy

    repo = ContentRepository(db)
    template = await repo.get_template(payload.industry_template_id)
    if template is None or template.status != "published":
        raise _content_error(404, "CONTENT_INDUSTRY_TEMPLATE_NOT_FOUND", "行业模板不存在或未发布")

    workflow_version = await repo.get_workflow(template.default_workflow_version_id)
    if workflow_version is None or workflow_version.status != "published":
        raise _content_error(409, "CONTENT_WORKFLOW_VERSION_MISSING", "行业模板锁定的工作流版本不存在或未发布")
    schema_version = int((workflow_version.definition_json or {}).get("schema_version") or 1)
    if schema_version != 3:
        raise _content_error(409, "CONTENT_WORKFLOW_V3_REQUIRED", "新任务只能使用 V3 工作流")
    from yuxi.content.v3.joint_workflow import (
        PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID,
        PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID,
        PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID,
        PLATFORM_WORKFLOW_PRICE_RECOVERY_ID,
        PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID,
        PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID,
    )

    if workflow_version.id not in {
        *SINGLE_BLUEPRINT_WORKFLOW_IDS,
        PLATFORM_WORKFLOW_PRICE_RECOVERY_ID,
        PLATFORM_WORKFLOW_VIRAL_AUTHOR_ID,
        PLATFORM_WORKFLOW_MODULAR_AUTHOR_ID,
        PLATFORM_WORKFLOW_EXPRESSION_GUIDANCE_ID,
        PLATFORM_WORKFLOW_DETERMINISTIC_PLAN_ID,
        PLATFORM_WORKFLOW_STANDARDIZED_FACTORY_ID,
    }:
        raise _content_error(409, "CONTENT_WORKFLOW_UPGRADE_REQUIRED", "新任务只能使用爆款仿写工作流")

    rule_version = await repo.get_published_rule_version(schema_version=schema_version)
    if rule_version is None:
        raise _content_error(
            503,
            "CONTENT_RULES_NOT_INITIALIZED",
            "V3 创作规则库尚未发布",
        )
    locked_workflow_hash = workflow_version.definition_hash or workflow_definition_hash(
        workflow_version.definition_json or {}
    )
    goal = str(payload.content_goal or "").strip()
    if not goal:
        raise _content_error(422, "CONTENT_GOAL_REQUIRED", "请选择内容目标")
    if goal not in {item["code"] for item in CONTENT_GOALS}:
        raise _content_error(422, "CONTENT_GOAL_INVALID", "内容目标无效")
    bundle = await repo.get_rule_bundle(rule_version.id)
    content_types = (bundle or {}).get("content_types") or []
    content_type_code = payload.content_type_code
    selection_policy = load_selection_policy()
    selection_policy_name = workflow_version.definition_json.get("selection_policy")
    joint = selection_policy_name in {
        "agent_skill_v1",
        "blueprint_first_v1",
        "modular_viral_author_v1",
    }
    deterministic_plan = selection_policy_name in {
        "deterministic_creation_plan_v1",
        "standardized_factory_v1",
    }
    mode = selection_policy["industry_modes"].get(template.slug, selection_policy["default_mode"])
    automatic = selection_policy_name in {
        "blueprint_first_v1",
        "modular_viral_author_v1",
        "deterministic_creation_plan_v1",
        "standardized_factory_v1",
    }
    if automatic and not content_type_code:
        content_type_code = None
    if content_types and (not automatic or content_type_code) and (not joint or mode == "direction_scoped"):
        type_map = {item["code"]: item for item in content_types}
        scoped_direction_codes = _scoped_content_type_codes(bundle or {}, template.slug, goal)
        if content_type_code is None:
            content_type_code = next(
                (item["code"] for item in content_types if goal in (item.get("supported_goals") or [])),
                content_types[0]["code"],
            )
        selected_type = type_map.get(content_type_code)
        if selected_type is None:
            raise _content_error(422, "CONTENT_TYPE_INVALID", "内容类型不存在或未发布")
        if goal not in (selected_type.get("supported_goals") or []) and content_type_code not in scoped_direction_codes:
            raise _content_error(422, "CONTENT_TYPE_GOAL_MISMATCH", "内容类型不支持当前内容目标")

    industry_pack = await repo.get_published_industry_pack(template.slug, schema_version=3)
    if industry_pack is None or industry_pack.status != "published" or industry_pack.slug != template.slug:
        raise _content_error(422, "CONTENT_INDUSTRY_PACK_INVALID", "行业内容包不存在、未发布或与行业不匹配")
    if industry_pack.schema_version != schema_version:
        raise _content_error(422, "CONTENT_INDUSTRY_PACK_VERSION_MISMATCH", "行业内容包与工作流版本不匹配")
    bound_rule_id = (industry_pack.source_metadata or {}).get("rule_version_id")
    if bound_rule_id and bound_rule_id != rule_version.id:
        raise _content_error(409, "CONTENT_INDUSTRY_RULE_BINDING_MISMATCH", "行业包尚未同步当前规则版本")

    channel_profile_version_id = payload.channel_profile_version_id or (template.default_strategy or {}).get(
        "channel_profile_version_id"
    )
    channel_version = None
    if channel_profile_version_id:
        channel_version = await repo.get_channel_version(channel_profile_version_id)
        if channel_version is None or channel_version.status != "published":
            raise _content_error(422, "CONTENT_CHANNEL_PROFILE_INVALID", "渠道配置不存在或未发布")

    persona = None
    if payload.persona_profile_version_id:
        persona = await repo.get_persona_version_for_user(payload.persona_profile_version_id, user)
        if persona is None or persona[0].status != "published":
            raise _content_error(422, "CONTENT_PERSONA_INVALID", "人设档案不存在、未发布或无权访问")

    runtime_snapshot = {
        "schema_version": schema_version,
        "rule_version_id": rule_version.id,
        "industry_template_version_id": template.id,
        "workflow_version_id": workflow_version.id,
        "workflow_definition_hash": locked_workflow_hash,
        "industry_pack_version_id": industry_pack.id if industry_pack else None,
        "persona_profile_version_id": payload.persona_profile_version_id,
        "channel_profile_version_id": channel_profile_version_id,
        "content_type_code": content_type_code,
        "creation_mode": payload.creation_mode,
        **(
            {"selection_policy_snapshot": selection_policy, "strategy_mode": mode}
            if joint or deterministic_plan
            else {}
        ),
    }
    task = await repo.create_task(
        task_id=f"ct_{uuid.uuid4().hex}",
        user=user,
        name=payload.name or _task_name(template.name, goal),
        template=template,
        rule_version_id=rule_version.id,
        mode=payload.mode,
        content_goal=goal,
        project_id=payload.project_id,
        content_type_code=content_type_code,
        industry_pack_version_id=industry_pack.id if industry_pack else None,
        persona_profile_version_id=payload.persona_profile_version_id,
        channel_profile_version_id=channel_profile_version_id,
        workflow_definition_hash=locked_workflow_hash,
        workflow_version=workflow_version,
        runtime_config_snapshot=runtime_snapshot,
    )
    await repo.track(
        "content_task_created",
        uid=str(user.uid),
        task_id=task.id,
        properties={
            "industry": template.slug,
            "mode": task.mode,
            "content_goal": goal,
            "content_type_code": content_type_code,
            "creation_mode": payload.creation_mode,
            "schema_version": runtime_snapshot["schema_version"],
        },
    )
    await db.commit()
    return {"task": task.to_dict(), "template": {"id": template.id, "name": template.name, "slug": template.slug}}


async def list_content_tasks(
    db: AsyncSession, user: User, *, page: int, page_size: int, status: str | None, generated_only: bool = False
) -> dict[str, Any]:
    repo = ContentRepository(db)
    items, total = await repo.list_tasks(
        user=user, page=page, page_size=page_size, status=status, generated_only=generated_only
    )
    return {"items": [item.to_dict() for item in items], "total": total, "page": page, "page_size": page_size}


async def get_content_task(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    template = await repo.get_template(task.industry_template_version_id)
    artifact = await repo.get_artifact_for_task(task.id)
    return {
        "task": task.to_dict(),
        "template": None
        if template is None
        else {
            "id": template.id,
            "slug": template.slug,
            "name": template.name,
            "quick_form_schema": template.quick_form_schema or [],
            "pro_form_schema": template.pro_form_schema or [],
            "review_policy": template.review_policy or {},
        },
        "artifact": artifact.to_dict() if artifact else None,
    }


async def update_content_task(db: AsyncSession, user: User, task_id: str, payload: ContentTaskUpdate) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_v3_task(task)
    changes = payload.model_dump(exclude_none=True)
    if "content_goal" in changes and changes["content_goal"] not in {item["code"] for item in CONTENT_GOALS}:
        raise _content_error(422, "CONTENT_GOAL_INVALID", "内容目标无效")
    next_goal = changes.get("content_goal", task.content_goal)
    next_type = changes.get("content_type_code", task.content_type_code)
    direction_scoped = (task.runtime_config_snapshot_json or {}).get(
        "strategy_mode", "direction_scoped"
    ) == "direction_scoped"
    if "content_type_code" in changes and direction_scoped:
        definition = await repo.get_content_type(task.rule_version_id, changes["content_type_code"])
        if definition is None:
            raise _content_error(422, "CONTENT_TYPE_INVALID", "内容类型不存在或未发布")
        template = await repo.get_template(task.industry_template_version_id)
        bundle = await repo.get_rule_bundle(task.rule_version_id)
        scoped_direction_codes = _scoped_content_type_codes(bundle or {}, template.slug if template else "", next_goal)
        if (
            next_goal not in (definition.supported_goals or [])
            and changes["content_type_code"] not in scoped_direction_codes
        ):
            raise _content_error(422, "CONTENT_TYPE_GOAL_MISMATCH", "内容类型不支持当前内容目标")
    elif "content_goal" in changes and next_type and direction_scoped:
        definition = await repo.get_content_type(task.rule_version_id, next_type)
        if definition and next_goal not in (definition.supported_goals or []):
            raise _content_error(422, "CONTENT_TYPE_GOAL_MISMATCH", "当前内容类型不支持新的内容目标")
    if "persona_profile_version_id" in changes:
        persona = await repo.get_persona_version_for_user(changes["persona_profile_version_id"], user)
        if persona is None or persona[0].status != "published":
            raise _content_error(422, "CONTENT_PERSONA_INVALID", "人设档案不存在、未发布或无权访问")
    if "channel_profile_version_id" in changes:
        channel = await repo.get_channel_version(changes["channel_profile_version_id"])
        if channel is None or channel.status != "published":
            raise _content_error(422, "CONTENT_CHANNEL_PROFILE_INVALID", "渠道配置不存在或未发布")
    for key, value in changes.items():
        setattr(task, key, value)
    task.updated_by = str(user.uid)
    task.updated_at = utc_now_naive()
    strategy_fields = {
        "content_goal",
        "content_type_code",
        "persona_profile_version_id",
        "channel_profile_version_id",
        "mode",
    }
    if strategy_fields & changes.keys():
        task.strategy_json = {}
        task.current_stage = "brief"
        task.runtime_config_snapshot_json = {
            **(task.runtime_config_snapshot_json or {}),
            "content_goal": task.content_goal,
            "content_type_code": task.content_type_code,
            "persona_profile_version_id": task.persona_profile_version_id,
            "channel_profile_version_id": task.channel_profile_version_id,
        }
    await db.commit()
    return {"task": task.to_dict()}


async def delete_content_task(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    task.deleted_at = utc_now_naive()
    task.status = "deleted"
    task.updated_by = str(user.uid)
    await db.commit()
    return {"deleted": True, "task_id": task.id}


async def delete_content_tasks(db: AsyncSession, user: User, task_ids: list[str]) -> dict[str, Any]:
    unique_task_ids = list(dict.fromkeys(task_ids))
    repo = ContentRepository(db)
    tasks = []
    for task_id in unique_task_ids:
        task = await repo.get_task_for_user(task_id, user, for_update=True)
        if task is None:
            raise _content_error(404, "CONTENT_TASK_NOT_FOUND", f"内容任务不存在: {task_id}")
        tasks.append(task)

    deleted_at = utc_now_naive()
    for task in tasks:
        task.deleted_at = deleted_at
        task.status = "deleted"
        task.updated_by = str(user.uid)
    await db.commit()
    return {"deleted": True, "task_ids": unique_task_ids, "deleted_count": len(unique_task_ids)}


async def duplicate_content_task(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    source = await repo.get_task_for_user(task_id, user)
    if source is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_v3_task(source)
    template = await repo.get_template(source.industry_template_version_id)
    if template is None:
        raise _content_error(409, "CONTENT_TEMPLATE_VERSION_MISSING", "原任务的行业模板版本不存在")
    copy_task = await repo.create_task(
        task_id=f"ct_{uuid.uuid4().hex}",
        user=user,
        name=f"{source.name}（副本）",
        template=template,
        rule_version_id=source.rule_version_id,
        mode=source.mode,
        content_goal=source.content_goal,
        project_id=source.project_id,
        content_type_code=source.content_type_code,
        industry_pack_version_id=source.industry_pack_version_id,
        persona_profile_version_id=source.persona_profile_version_id,
        channel_profile_version_id=source.channel_profile_version_id,
        workflow_definition_hash=source.workflow_definition_hash,
        runtime_config_snapshot=deepcopy(source.runtime_config_snapshot_json or {}),
    )
    copy_task.brief_json = deepcopy(source.brief_json or {})
    copy_task.strategy_json = {}
    copy_task.evidence_json = deepcopy(source.evidence_json or {})
    copy_task.selected_angle_json = {}
    copy_task.primary_narrative_axis = None
    copy_task.selected_image_item_id = source.selected_image_item_id
    copy_task.selected_poster_template_id = source.selected_poster_template_id
    copy_task.current_stage = "generation" if copy_task.brief_json else "brief"
    await db.commit()
    return {"task": copy_task.to_dict()}


async def save_content_brief(
    db: AsyncSession, user: User, task_id: str, brief: ContentBriefPayload, *, compile_now: bool
) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_v3_task(task)
    template = await repo.get_template(task.industry_template_version_id)
    if template is None:
        raise _content_error(409, "CONTENT_TEMPLATE_VERSION_MISSING", "任务绑定的行业模板版本不存在")
    raw_brief = brief.model_dump()
    raw_user_request = str(
        raw_brief.get("user_request") or (raw_brief.get("form_values") or {}).get("user_request") or ""
    ).strip()
    quote_case = (
        _parse_content_studio_quote_case(raw_user_request, content_type_code=task.content_type_code)
        if raw_user_request
        else None
    )
    form_values = raw_brief.get("form_values") or {}
    service_entry = form_values.get("mp_service_entry")
    content_type_id = str(form_values.get("mp_content_type_id") or "").strip()
    if compile_now and service_entry == "装修家居" and not content_type_id:
        raise _content_error(422, "CONTENT_TYPE_REQUIRED", "请选择内容类型")
    form_fields = None
    if service_entry in SERVICE_ENTRIES:
        form_fields = configured_business_variable_fields(
            (await list_business_variables(db))["business_variables"],
            service_entry=str(service_entry),
            content_type_id=content_type_id or None,
            port="pc",
            select_options=catalog_select_options(
                target_audiences=await list_enabled_target_audience_names(db),
                resident_populations=await list_enabled_resident_population_names(db),
                process_types=await list_enabled_process_type_names(db),
                process_names_by_type=await list_enabled_process_names_by_type(db),
            ),
        )
    compiled, missing = compile_content_brief(task=task, template=template, brief=brief)
    if compile_now and form_fields:
        for field in form_fields:
            if not field.get("required"):
                continue
            value = (compiled.get("form_values") or {}).get(field["key"])
            if value in (None, "", []):
                missing.append({"field": field["key"], "label": field.get("label") or field["key"]})
    direction = (
        content_direction_from_form_values(form_values)
        or compiled.get("content_type_code")
        or raw_brief.get("content_type_code")
    )
    if direction:
        compiled["content_type_code"] = direction
        task.content_type_code = direction
    selection = brief.visual_material
    requested_cover_mode = selection.cover_mode if selection else None
    requested_image_item_id = selection.image_item_id if selection else None
    requested_poster_template_id = selection.poster_template_id if selection else None
    requested_hycanvas_template_id = selection.hycanvas_template_id if selection else None
    requested_featured_cover_template_id = selection.featured_cover_template_id if selection else None
    if requested_hycanvas_template_id and requested_featured_cover_template_id:
        raise _content_error(
            422,
            "CONTENT_COVER_TEMPLATE_CONFLICT",
            "内置封面与精选封面只能选择其一",
        )
    if requested_cover_mode == "ai" and (
        requested_poster_template_id or requested_hycanvas_template_id or requested_featured_cover_template_id
    ):
        raise _content_error(
            422,
            "CONTENT_AI_COVER_TEMPLATE_CONFLICT",
            "AI 封面只使用图库原图，不能选择或叠加封面模板",
        )
    hycanvas_template = None
    if compile_now and requested_hycanvas_template_id:
        from yuxi.services.hycanvas_service import HyCanvasClient

        hycanvas_template = await HyCanvasClient.from_env().get_xiaohongshu_template(requested_hycanvas_template_id)
        if hycanvas_template is None or hycanvas_template.get("zone") != "builtin":
            raise _content_error(
                422,
                "CONTENT_HYCANVAS_TEMPLATE_INVALID",
                "所选内置封面模板不存在或不可用",
            )
    is_handwritten_quote_template = bool(hycanvas_template and hycanvas_template.get("is_handwritten_quote_template"))
    existing_quote_snapshot = (task.runtime_config_snapshot_json or {}).get("trusted_external_material_snapshot")
    has_trusted_quote = quote_case is not None or (
        isinstance(existing_quote_snapshot, dict)
        and existing_quote_snapshot.get("quote_block")
        and existing_quote_snapshot.get("title_price")
        and (
            existing_quote_snapshot.get("source") == "dangjia"
            or existing_quote_snapshot.get("sanitized_user_request") == raw_user_request
        )
    )
    if is_handwritten_quote_template and not has_trusted_quote:
        raise _content_error(
            422,
            "CONTENT_HANDWRITTEN_QUOTE_REQUIRED",
            "手写报价封面需要已确认的结构化施工报价数据",
        )
    effective_image_item_id = None if is_handwritten_quote_template else requested_image_item_id
    if (
        compile_now
        and (requested_cover_mode == "ai" or requested_hycanvas_template_id or requested_featured_cover_template_id)
        and not effective_image_item_id
        and not is_handwritten_quote_template
    ):
        raise _content_error(
            422,
            "CONTENT_IMAGE_MATERIAL_REQUIRED",
            "请选择一张图库图片作为封面背景图",
        )
    requested_composition = (
        None
        if is_handwritten_quote_template
        else (selection.photo_composition.model_dump() if selection and selection.photo_composition else None)
    )
    if requested_cover_mode == "ai" and requested_composition:
        raise _content_error(
            422,
            "CONTENT_AI_COVER_COMPOSITION_UNSUPPORTED",
            "AI 封面只支持一张封面原图，不能使用图片组合",
        )
    current_visual_material = (getattr(task, "brief_json", None) or {}).get("visual_material") or {}
    if task.current_stage != "brief" and (
        task.selected_image_item_id != effective_image_item_id
        or task.selected_poster_template_id != requested_poster_template_id
        or current_visual_material.get("cover_mode") != requested_cover_mode
        or current_visual_material.get("hycanvas_template_id") != requested_hycanvas_template_id
        or current_visual_material.get("featured_cover_template_id") != requested_featured_cover_template_id
        or current_visual_material.get("photo_composition") != requested_composition
    ):
        raise _content_error(
            409,
            "CONTENT_VISUAL_MATERIAL_LOCKED",
            "视觉素材已随事实简报锁定，不能在 V3 生产开始后更换",
        )

    visual_snapshot: dict[str, Any] | None = (
        {}
        if effective_image_item_id
        or requested_poster_template_id
        or requested_hycanvas_template_id
        or requested_featured_cover_template_id
        or requested_cover_mode
        else None
    )
    if effective_image_item_id:
        owner_uid = str(user.uid)
        material_repo = MaterialLibraryRepository(db, include_shared=True)
        image_item = await material_repo.get_item_for_user(effective_image_item_id, owner_uid, for_update=True)
        if image_item is None or image_item.material_type != "image" or image_item.status != "enabled":
            raise _content_error(
                422,
                "CONTENT_IMAGE_MATERIAL_INVALID",
                "所选图库图片不存在、已停用或无权访问",
            )
        image_asset = await material_repo.get_asset(image_item.asset_id, image_item.owner_uid, for_update=True)
        if image_asset is None or image_asset.role not in {"source", "library_image"}:
            raise _content_error(422, "CONTENT_IMAGE_ASSET_INVALID", "所选图库图片的文件记录无效")
        await ContentCoverRepository(db).retain_material_use([image_asset.id], owner_uid)
        visual_snapshot = {
            "cover_mode": requested_cover_mode,
            "image_item_id": image_item.id,
            "image_asset_id": image_asset.id,
            "image_name": image_item.display_name,
            "image_category_id": image_item.category,
            "image_sha256": image_asset.sha256,
            "image_width": image_asset.image_width,
            "image_height": image_asset.image_height,
        }
        if requested_poster_template_id:
            poster = await ContentCoverRepository(db).get_poster_template_for_user(
                requested_poster_template_id, owner_uid, for_update=True
            )
            if poster is None or poster.status != "ready" or not poster.product_box_json:
                raise _content_error(
                    422,
                    "CONTENT_POSTER_TEMPLATE_INVALID",
                    "所选封面模板不存在、未启用、未完成标注或无权访问",
                )
            poster_asset = await material_repo.get_asset(poster.asset_id, owner_uid, for_update=True)
            if poster_asset is None or poster_asset.role != "poster_template":
                raise _content_error(422, "CONTENT_POSTER_TEMPLATE_ASSET_INVALID", "所选封面模板文件记录无效")
            poster_library_item = await material_repo.get_item_by_asset(poster.asset_id)
            if (
                poster_library_item is None
                or poster_library_item.owner_uid != owner_uid
                or poster_library_item.material_type != "cover_template"
                or poster_library_item.status != "enabled"
            ):
                raise _content_error(422, "CONTENT_POSTER_TEMPLATE_INVALID", "所选封面模板已从素材库停用或移除")
            visual_snapshot.update(
                {
                    "poster_template_id": poster.id,
                    "poster_template_asset_id": poster_asset.id,
                    "poster_template_name": poster_library_item.display_name,
                    "poster_template_checksum": poster.checksum,
                    "poster_template_version": poster.version,
                }
            )
    if requested_composition:
        from yuxi.services.content_photo_composition import resolve_photo_composition

        if not effective_image_item_id or not (requested_hycanvas_template_id or requested_featured_cover_template_id):
            raise _content_error(422, "CONTENT_COMPOSITION_TEMPLATE_REQUIRED", "图片组合需要选择首图和封面模板")
        visual_snapshot["photo_composition"] = await resolve_photo_composition(
            db,
            user,
            selection.photo_composition,
            effective_image_item_id,
            complete=compile_now,
        )
    if compile_now and (requested_hycanvas_template_id or requested_featured_cover_template_id):
        if requested_hycanvas_template_id:
            visual_snapshot.update(
                {
                    "hycanvas_template_id": hycanvas_template["id"],
                    "hycanvas_template_title": hycanvas_template["title"],
                    "hycanvas_fillable_fields": hycanvas_template["fillable_fields"],
                    "is_handwritten_quote_template": is_handwritten_quote_template,
                }
            )
        if requested_featured_cover_template_id:
            from yuxi.services.hycanvas_service import HyCanvasClient

            template_catalog = await HyCanvasClient.from_env().list_xiaohongshu_templates()
            catalog_by_id = {item["id"]: item for item in template_catalog["templates"]}
            featured_template = catalog_by_id.get(requested_featured_cover_template_id)
            if featured_template is None or featured_template.get("zone") != "featured":
                raise _content_error(
                    422,
                    "CONTENT_FEATURED_COVER_TEMPLATE_INVALID",
                    "所选精选封面不存在或不可用",
                )
            visual_snapshot.update(
                {
                    "featured_cover_template_id": featured_template["id"],
                    "featured_cover_template_title": featured_template["title"],
                }
            )
    task.selected_image_item_id = effective_image_item_id
    task.selected_poster_template_id = requested_poster_template_id
    runtime_snapshot = dict(task.runtime_config_snapshot_json or {})
    original_request = compiled.get("original_user_request") or raw_user_request
    try:
        original_json = json.loads(original_request)
    except (ValueError, TypeError):
        original_json = None
    previous_quote = runtime_snapshot.get("trusted_external_material_snapshot") or {}
    if not (
        runtime_snapshot.get("raw_business_json")
        and previous_quote.get("sanitized_user_request") == raw_user_request
        and quote_case is None
    ):
        runtime_snapshot["raw_business_json"] = (
            original_json if isinstance(original_json, dict) else brief.model_dump(mode="json", exclude_unset=True)
        )
    trusted_snapshot_key = "trusted_external_material_snapshot"
    if quote_case is not None:
        trusted_snapshot_key = quote_case["trusted_snapshot_key"]
        runtime_snapshot[trusted_snapshot_key] = quote_case["trusted_snapshot"]
    else:
        existing_quote_snapshot = runtime_snapshot.get(trusted_snapshot_key)
        if (
            isinstance(existing_quote_snapshot, dict)
            and existing_quote_snapshot.get("ingestion_channel") == "content_studio_case"
            and existing_quote_snapshot.get("sanitized_user_request") != raw_user_request
        ):
            runtime_snapshot.pop(trusted_snapshot_key, None)
    task.runtime_config_snapshot_json = runtime_snapshot
    compiled["visual_material"] = (
        {
            "cover_mode": requested_cover_mode,
            "image_item_id": visual_snapshot.get("image_item_id"),
            "image_asset_id": visual_snapshot.get("image_asset_id"),
            "image_name": visual_snapshot.get("image_name"),
            "image_category_id": visual_snapshot.get("image_category_id"),
            "poster_template_id": visual_snapshot.get("poster_template_id"),
            "poster_template_name": visual_snapshot.get("poster_template_name"),
            "hycanvas_template_id": requested_hycanvas_template_id,
            "hycanvas_template_title": visual_snapshot.get("hycanvas_template_title"),
            "is_handwritten_quote_template": visual_snapshot.get("is_handwritten_quote_template", False),
            "featured_cover_template_id": requested_featured_cover_template_id,
            "featured_cover_template_title": visual_snapshot.get("featured_cover_template_title"),
            "photo_composition": requested_composition,
        }
        if visual_snapshot
        else None
    )
    task.brief_json = compiled
    task.updated_by = str(user.uid)
    task.updated_at = utc_now_naive()
    if compile_now and missing:
        await db.commit()
        raise _content_error(
            422,
            "CONTENT_REQUIRED_VARIABLE_MISSING",
            "业务简报缺少必填变量",
            fields=missing,
            suggested_action="补充缺失字段后重新编译",
        )
    if compile_now:
        task.runtime_config_snapshot_json = {
            **runtime_snapshot,
            "visual_material": visual_snapshot,
        }
        task.evidence_json = normalize_manual_evidence(task.id, compiled)
        task.status = "brief_ready"
        task.current_stage = "generation"
        task.strategy_json = {}
        await repo.track("content_brief_completed", uid=str(user.uid), task_id=task.id)
    await db.commit()
    return {"task": task.to_dict(), "missing_fields": missing, "compiled": compile_now and not missing}


def _run_response(run: AgentRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "task_id": run.thread_id,
        "status": run.status,
        "request_id": run.request_id,
        "stream_url": f"/api/content/runs/{run.id}/events",
    }


async def _enqueue_content_run(
    db: AsyncSession,
    *,
    user: User,
    task: ContentTask,
    request_id: str,
    action: str,
    model_spec: str | None,
    parent_run_id: str | None = None,
    resume: dict[str, Any] | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    if task.selected_image_item_id:
        occupying = await MaterialLibraryRepository(db, include_shared=True).item_is_selected_by_task(
            task.selected_image_item_id,
            str(user.uid),
            exclude_task_id=task.id,
        )
        if occupying:
            raise _content_error(409, "CONTENT_COVER_IN_USE", "该图库图片已被其他内容任务使用")
    run_repo = AgentRunRepository(db)
    existing = await run_repo.get_run_by_request_id(request_id)
    if existing:
        if existing.uid != str(user.uid):
            raise _content_error(409, "CONTENT_REQUEST_ID_CONFLICT", "request_id 已被其他用户使用")
        return _run_response(existing)
    run_id = str(uuid.uuid4())
    input_payload = {
        "run_type": "content_resume" if action == "resume" else "content",
        "task_id": task.id,
        "action": action,
        "model_spec": model_spec,
        "uid": str(user.uid),
        "request_id": request_id,
        "resume": resume,
        "node_id": node_id,
        "parent_run_id": parent_run_id,
        "workflow_version_id": task.workflow_version_id,
        "rule_version_id": task.rule_version_id,
    }
    try:
        checkpoint_thread_id = f"content:{run_id}"
        if action in {"resume", "retry"} and parent_run_id:
            parent = await run_repo.get_run(parent_run_id)
            if parent and parent.checkpoint_thread_id:
                checkpoint_thread_id = parent.checkpoint_thread_id
        run = await run_repo.create_run(
            run_id=run_id,
            thread_id=task.id,
            agent_id="content-studio",
            uid=str(user.uid),
            request_id=request_id,
            input_payload=input_payload,
            parent_run_id=parent_run_id,
            run_type=input_payload["run_type"],
            resume_request_id=request_id if action == "resume" else None,
            checkpoint_thread_id=checkpoint_thread_id,
        )
        task.latest_run_id = run.id
        task.status = "queued"
        task.current_stage = "generation"
        task.error_json = None
        run_event = {
            "start": "content_run_started",
            "resume": "content_run_resumed",
            "retry": "content_run_retried",
        }[action]
        await ContentRepository(db).track(
            run_event,
            uid=str(user.uid),
            task_id=task.id,
            run_id=run.id,
            properties={"parent_run_id": parent_run_id, "node_id": node_id},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await run_repo.get_run_by_request_id(request_id)
        if existing and existing.uid == str(user.uid):
            return _run_response(existing)
        raise _content_error(409, "CONTENT_REQUEST_ID_CONFLICT", "request_id 冲突")
    queue = await get_arq_pool()
    await queue.enqueue_job("process_content_run", run.id, _job_id=f"content-run:{run.id}")
    return _run_response(run)


async def create_content_run(db: AsyncSession, user: User, task_id: str, payload: ContentRunCreate) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_runnable_v3_task(task)
    if not task.brief_json:
        raise _content_error(409, "CONTENT_TASK_NOT_READY", "请先完成业务简报")
    model_spec = _validate_model_spec(payload.model_spec)
    result = await _enqueue_content_run(
        db,
        user=user,
        task=task,
        request_id=payload.request_id,
        action="start",
        model_spec=model_spec,
    )
    return result


async def resume_content_run(db: AsyncSession, user: User, run_id: str, payload: ContentRunResume) -> dict[str, Any]:
    run_repo = AgentRunRepository(db)
    parent = await run_repo.get_run_for_user(run_id, str(user.uid))
    if parent is None or parent.run_type not in {"content", "content_resume"}:
        raise _content_error(404, "CONTENT_RUN_NOT_FOUND", "内容运行不存在")
    if parent.status != "interrupted":
        raise _content_error(409, "CONTENT_RUN_NOT_INTERRUPTED", "只有等待人工处理的运行可以恢复")
    task = await ContentRepository(db).get_task_for_user(parent.thread_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_runnable_v3_task(task)
    model_spec = (parent.input_payload or {}).get("model_spec")
    return await _enqueue_content_run(
        db,
        user=user,
        task=task,
        request_id=payload.request_id,
        action="resume",
        model_spec=model_spec,
        parent_run_id=parent.id,
        resume=payload.resume,
    )


async def retry_content_node(
    db: AsyncSession,
    user: User,
    run_id: str,
    *,
    request_id: str,
    node_id: str | None,
    model_spec: str | None,
) -> dict[str, Any]:
    run_repo = AgentRunRepository(db)
    parent = await run_repo.get_run_for_user(run_id, str(user.uid))
    if parent is None:
        raise _content_error(404, "CONTENT_RUN_NOT_FOUND", "内容运行不存在")
    if parent.status != "failed":
        raise _content_error(409, "CONTENT_RUN_NOT_RETRYABLE", "只有失败的运行可以从失败节点重试")
    task = await ContentRepository(db).get_task_for_user(parent.thread_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_runnable_v3_task(task)
    return await _enqueue_content_run(
        db,
        user=user,
        task=task,
        request_id=request_id,
        action="retry",
        model_spec=_validate_model_spec(model_spec) or (parent.input_payload or {}).get("model_spec"),
        parent_run_id=parent.id,
        node_id=node_id,
    )


async def get_content_run(db: AsyncSession, user: User, run_id: str) -> dict[str, Any]:
    run_repo = AgentRunRepository(db)
    run = await run_repo.get_run_for_user(run_id, str(user.uid))
    if run is None or run.run_type not in {"content", "content_resume"}:
        raise _content_error(404, "CONTENT_RUN_NOT_FOUND", "内容运行不存在")
    root, content_runs, delegated_runs = await run_repo.list_content_run_family(run)
    run_ids = [item.id for item in content_runs]
    projection = await ContentRepository(db).get_v3_run_projection(
        task_id=run.thread_id,
        run_ids=run_ids,
    )

    async def collect_events(items: list[AgentRun]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for item in items:
            for event in await list_run_stream_events(item.id, limit=500):
                envelope = event.get("payload") or {}
                event_payload = envelope.get("payload") if isinstance(envelope, dict) else {}
                event_type = str(event.get("event_type") or "")
                if not event_type.startswith("content.") and not (
                    event_type == "custom" and (event_payload or {}).get("name") == "content.node"
                ):
                    continue
                collected.append(
                    {
                        "seq": event.get("seq"),
                        "run_id": item.id,
                        "event_type": event_type,
                        "created_at": envelope.get("created_at") if isinstance(envelope, dict) else None,
                        "payload": event_payload or {},
                    }
                )
        return collected

    events = await collect_events(content_runs)
    has_mirrored_runtime = any(
        item["event_type"].startswith(("content.agent.", "content.skill.", "content.tool.", "content.knowledge."))
        for item in events
    )
    if not has_mirrored_runtime:
        events.extend(await collect_events(delegated_runs))
    events.sort(key=lambda item: (item.get("created_at") or "", item.get("seq") or ""))
    knowledge_events = [item for item in events if item["event_type"] == "content.knowledge.retrieved"]
    skill_events = [item for item in events if item["event_type"] == "content.skill.activated"]
    tool_events = [item for item in events if item["event_type"].startswith("content.tool.")]
    return {
        "run": run.to_dict(),
        "root_run_id": root.id,
        "continuations": [item.to_dict() for item in content_runs],
        "nodes": projection["nodes"],
        "match_decision": projection["match_decision"],
        "formula_selection": projection["formula_selection"],
        "delegated_agents": [
            {
                "run_id": item.id,
                "agent_slug": item.agent_id,
                "status": item.status,
                "parent_run_id": item.parent_agent_run_id,
                "node_id": (item.input_payload or {}).get("node_id"),
                "runtime_config_snapshot": (item.input_payload or {}).get("runtime_config_snapshot"),
                "error_type": item.error_type,
                "error_message": item.error_message,
                "started_at": format_utc_datetime(item.started_at),
                "finished_at": format_utc_datetime(item.finished_at),
            }
            for item in delegated_runs
        ],
        "external_wait": projection["external_wait"],
        "evidence": projection["evidence"],
        "event_summary": {
            "agent_run_count": len(delegated_runs),
            "skill_activation_count": len(skill_events),
            "tool_event_count": len(tool_events),
            "knowledge_retrieval_count": len(knowledge_events),
            "knowledge_result_count": sum(
                int((item["payload"] or {}).get("result_count") or 0) for item in knowledge_events
            ),
        },
        "events": events,
    }


async def get_task_artifact(db: AsyncSession, user: User, task_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    task = await repo.get_task_for_user(task_id, user)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    artifact = await repo.get_artifact_for_task(task.id)
    return {"artifact": artifact.to_dict() if artifact else None}


async def get_artifact_viral_reference(db: AsyncSession, user: User, artifact_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    if (artifact.runtime_config_snapshot or {}).get("creation_mode") != "viral_rewrite":
        raise _content_error(409, "VIRAL_REFERENCE_NOT_AVAILABLE", "当前内容不是爆款仿写")

    selected = next(
        (
            item
            for item in (artifact.evidence_snapshot or {}).get("items") or []
            if (item.get("metadata") or {}).get("selected_reference") is True
        ),
        None,
    )
    if selected is None:
        raise _content_error(404, "VIRAL_REFERENCE_NOT_FOUND", "未找到本次仿写选中的爆款参考")

    asset_id = (selected.get("metadata") or {}).get("asset_id")
    if asset_id:
        from yuxi.services.content_viral_assets import require_asset

        # 新工作流的证据只包含结构蓝图，完整原文保存在选中的不可变资产版本中。
        asset = await require_asset(db, user, asset_id)
        source = asset.source_json
        return {
            "reference": {
                "id": asset.id,
                "content": f"{source['title']}\n\n{source['body']}",
                "source_name": source["title"],
                "knowledge_base_name": "",
            }
        }

    node_run = await repo.get_latest_completed_node_run(artifact.task_id, "collect_viral_candidates")
    if node_run is None:
        raise _content_error(404, "VIRAL_REFERENCE_SOURCE_NOT_FOUND", "未找到已选爆款的原文记录")
    collection = ((node_run.output_snapshot or {}).get("result") or {}).get("viral_candidate_collection") or {}
    candidate = next(
        (item for item in collection.get("evidence_items") or [] if item.get("id") == selected.get("id")),
        None,
    )
    if candidate is None or not str(candidate.get("value") or "").strip():
        raise _content_error(404, "VIRAL_REFERENCE_SOURCE_NOT_FOUND", "未找到已选爆款的原文记录")

    metadata = candidate.get("metadata") or {}
    return {
        "reference": {
            "id": candidate["id"],
            "content": str(candidate["value"]).strip(),
            "source_name": metadata.get("document_name") or metadata.get("source") or "爆款库",
            "knowledge_base_name": metadata.get("knowledge_base_name") or "",
        }
    }


async def regenerate_content_artifact(
    db: AsyncSession,
    user: User,
    artifact_id: str,
    payload: ContentArtifactRegenerate,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    task = await repo.get_task_for_user(artifact.task_id, user, for_update=True)
    if task is None:
        raise _content_error(404, "CONTENT_TASK_NOT_FOUND", "内容任务不存在")
    _require_v3_task(task)
    return await create_content_run(
        db,
        user,
        task.id,
        ContentRunCreate(request_id=payload.request_id, model_spec=payload.model_spec),
    )


async def create_content_rule_draft(
    db: AsyncSession,
    user: User,
    payload: RuleDraftCreate,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    current = await repo.get_published_rule_version_for_update(schema_version=3)
    existing = await repo.get_platform_rule_draft(schema_version=3)
    if existing:
        raise _content_error(
            409,
            "CONTENT_RULE_DRAFT_EXISTS",
            f"已有规则草稿 v{existing.version}，请继续编辑或先放弃该草稿",
            version_id=existing.id,
        )
    if current is None or current.id != payload.source_version_id:
        raise _content_error(409, "CONTENT_RULE_SOURCE_INVALID", "只能基于当前已发布的平台规则创建草稿")
    source = current
    source_bundle = await repo.get_rule_bundle(source.id, include_disabled=True)
    if source_bundle is None:
        raise _content_error(404, "CONTENT_RULE_VERSION_MISSING", "源规则版本不存在")

    version = await repo.next_platform_rule_version()
    version_id = f"content-rules-platform-v{version}"
    changelog = payload.changelog.strip() or f"基于 v{source.version} 创建运营编辑草稿"
    await repo.create_rule_version(
        version_id=version_id,
        version=version,
        changelog=changelog,
        created_by=str(user.uid),
    )
    await repo.replace_rule_bundle(version_id, source_bundle)
    await repo.track(
        "content_rule_draft_created",
        uid=str(user.uid),
        properties={"version_id": version_id, "version": version, "source_version_id": source.id},
    )
    await db.commit()
    bundle = await repo.get_rule_bundle(version_id, include_disabled=True)
    return {"bundle": bundle, "validation": validate_rule_bundle_for_publish(bundle or {})}


async def save_content_rule_draft(
    db: AsyncSession,
    user: User,
    version_id: str,
    payload: RuleBundleUpdate,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    version = await repo.get_rule_version_for_update(version_id)
    if version is None or version.tenant_id is not None:
        raise _content_error(404, "CONTENT_RULE_VERSION_MISSING", "平台规则版本不存在")
    if version.status != "draft":
        raise _content_error(409, "CONTENT_RULE_VERSION_IMMUTABLE", "已发布或已归档规则不可直接修改，请创建新草稿")

    bundle = normalize_rule_bundle(payload)
    await repo.replace_rule_bundle(version.id, bundle)
    version.changelog = bundle["changelog"] or version.changelog
    validation = validate_rule_bundle_for_publish(bundle)
    await repo.track(
        "content_rule_draft_saved",
        uid=str(user.uid),
        properties={
            "version_id": version.id,
            "version": version.version,
            "method_count": len(bundle["methods"]),
            "title_formula_count": len(bundle["title_formulas"]),
            "content_formula_count": len(bundle["content_formulas"]),
            "combination_count": len(bundle["combination_rules"]),
            "validation_error_count": len(validation["errors"]),
        },
    )
    await db.commit()
    saved = await repo.get_rule_bundle(version.id, include_disabled=True)
    return {"bundle": saved, "validation": validation}


async def discard_content_rule_draft(db: AsyncSession, user: User, version_id: str) -> dict[str, bool]:
    repo = ContentRepository(db)
    version = await repo.get_rule_version_for_update(version_id)
    if version is None or version.tenant_id is not None:
        raise _content_error(404, "CONTENT_RULE_VERSION_MISSING", "平台规则版本不存在")
    if version.status != "draft":
        raise _content_error(409, "CONTENT_RULE_VERSION_IMMUTABLE", "只能放弃尚未发布的规则草稿")
    await repo.track(
        "content_rule_draft_discarded",
        uid=str(user.uid),
        properties={"version_id": version.id, "version": version.version},
    )
    await repo.delete_rule_version(version.id)
    await db.commit()
    return {"discarded": True}


async def activate_content_rule_version(
    db: AsyncSession,
    user: User,
    version_id: str,
    *,
    rollback: bool,
    note: str | None,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    target = await repo.get_rule_version_for_update(version_id)
    if target is None or target.tenant_id is not None:
        raise _content_error(404, "CONTENT_RULE_VERSION_MISSING", "平台规则版本不存在")
    if rollback and target.status not in {"archived", "published"}:
        raise _content_error(409, "CONTENT_RULE_VERSION_NOT_ROLLBACKABLE", "只能回滚到已发布过的规则版本")
    if not rollback and target.status not in {"draft", "archived", "published"}:
        raise _content_error(409, "CONTENT_RULE_VERSION_NOT_PUBLISHABLE", "当前规则版本不可发布")

    bundle = await repo.get_rule_bundle(target.id, include_disabled=True)
    validation = validate_rule_bundle_for_publish(bundle or {})
    if validation["errors"]:
        raise _content_error(
            409,
            "CONTENT_RULE_VERSION_INVALID",
            "规则校验未通过，请修正后再发布",
            validation=validation,
        )

    current = await repo.get_published_rule_version_for_update(schema_version=3)
    if current and current.id != target.id:
        current.status = "archived"
    from yuxi.services.content_industry_sync import sync_industry_pack_bindings

    await sync_industry_pack_bindings(db, bundle=bundle, uid=str(user.uid))
    target.status = "published"
    target.published_at = utc_now_naive()
    await repo.track(
        "content_rule_version_rolled_back" if rollback else "content_rule_version_published",
        uid=str(user.uid),
        properties={
            "version_id": target.id,
            "version": target.version,
            "previous_version_id": current.id if current else None,
            "note": note,
        },
    )
    await db.commit()
    return {
        "version": {
            "id": target.id,
            "version": target.version,
            "status": target.status,
            "published_at": target.published_at.isoformat() if target.published_at else None,
        },
        "previous_version_id": current.id if current and current.id != target.id else None,
    }


async def activate_content_workflow_version(
    db: AsyncSession,
    user: User,
    version_id: str,
    *,
    rollback: bool,
    note: str | None,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    target = await repo.get_workflow_for_update(version_id)
    if target is None or target.tenant_id is not None:
        raise _content_error(404, "CONTENT_WORKFLOW_VERSION_MISSING", "平台工作流版本不存在")
    allowed_statuses = (
        {"archived", "published"} if rollback else {"draft", "validated", "canary", "archived", "published"}
    )
    if target.status not in allowed_statuses:
        raise _content_error(409, "CONTENT_WORKFLOW_VERSION_NOT_PUBLISHABLE", "当前工作流版本不可发布")

    definition = target.definition_json or {}
    agent_slugs = {
        item["agent_slug"]
        for item in definition.get("nodes") or []
        if isinstance(item, dict) and item.get("type") == "agent" and item.get("agent_slug")
    }
    skill_slugs = {
        slug
        for item in definition.get("nodes") or []
        if isinstance(item, dict)
        for slug in item.get("required_skills") or []
    }
    agents = await AgentRepository(db).list_by_slugs(sorted(agent_slugs))
    skills = await SkillRepository(db).list_by_slugs(sorted(skill_slugs))
    enabled_agents = {item.slug for item in agents if item.enabled}
    enabled_skills = {item.slug for item in skills if item.enabled}
    try:
        WorkflowDefinitionPolicy.validate(
            definition,
            catalog=WorkflowCatalog(agents=frozenset(enabled_agents), skills=frozenset(enabled_skills)),
        )
    except ValueError as exc:
        raise _content_error(
            409,
            "CONTENT_WORKFLOW_VERSION_INVALID",
            str(exc),
        ) from exc

    schema_version = 3
    current = await repo.get_published_workflow_for_update(target.slug, schema_version=3)
    if current and current.id != target.id:
        current.status = "archived"
    target.status = "published"
    target.definition_hash = workflow_definition_hash(definition)
    target.published_at = utc_now_naive()
    await repo.track(
        "content_workflow_version_rolled_back" if rollback else "content_workflow_version_published",
        uid=str(user.uid),
        properties={
            "version_id": target.id,
            "version": target.version,
            "schema_version": schema_version,
            "previous_version_id": current.id if current and current.id != target.id else None,
            "note": note,
        },
    )
    await db.commit()
    return {
        "version": {
            "id": target.id,
            "version": target.version,
            "status": target.status,
            "definition_hash": target.definition_hash,
            "published_at": format_utc_datetime(target.published_at),
        },
        "previous_version_id": current.id if current and current.id != target.id else None,
    }


async def validate_content_industry_pack(
    db: AsyncSession,
    user: User,
    version_id: str,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    record = await repo.get_industry_pack(version_id)
    if record is None or record.tenant_id is not None:
        raise _content_error(404, "CONTENT_INDUSTRY_PACK_MISSING", "平台行业包版本不存在")
    if record.schema_version != 3:
        raise _content_error(409, "CONTENT_INDUSTRY_PACK_V3_REQUIRED", "只能校验 V3 Industry Pack")
    mappings = await repo.list_industry_variable_mappings(record.id)
    groups = await repo.list_combination_groups(record.combination_overrides or [])
    rule_version_id = (record.source_metadata or {}).get("rule_version_id") or PLATFORM_RULE_V3_ID
    rule_bundle = await repo.get_rule_bundle(rule_version_id, include_disabled=True)
    if rule_bundle is None:
        raise _content_error(503, "CONTENT_RULES_NOT_INITIALIZED", "V3 平台规则尚未初始化")
    bound_ids = set(record.combination_overrides or [])
    available_ids = {
        item["id"]
        for item in rule_bundle["combination_rules"]
        if not item.get("industry_scope") or record.slug in item["industry_scope"]
    }
    if bound_ids - available_ids or bound_ids != {item["id"] for item in groups}:
        raise _content_error(
            409, "CONTENT_INDUSTRY_RULE_REFERENCE_INVALID", "行业包引用了其他规则版本、其他行业或不存在的组合"
        )
    report = ValidateIndustryPackHandler().execute(
        record=record,
        variable_mappings=mappings,
        combination_groups=groups,
        rule_bundle=rule_bundle,
    )
    previous_regression = (record.evaluation_report or {}).get("regression")
    if previous_regression:
        report["regression"] = previous_regression
    record.evaluation_report = report
    await repo.track(
        "content_industry_pack_validated",
        uid=str(user.uid),
        properties={
            "version_id": record.id,
            "slug": record.slug,
            "valid": report["validation"]["valid"],
            "evaluation_passed": report["evaluation"]["passed"],
        },
    )
    if commit:
        await db.commit()
    return report


async def transition_content_industry_pack(
    db: AsyncSession,
    user: User,
    version_id: str,
    payload: IndustryPackTransitionRequest,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    record = await repo.get_industry_pack_for_update(version_id)
    if record is None or record.tenant_id is not None:
        raise _content_error(404, "CONTENT_INDUSTRY_PACK_MISSING", "平台行业包版本不存在")
    try:
        IndustryPackPolicy.assert_transition(record.status, payload.target_status)
    except ValueError as exc:
        raise _content_error(409, "CONTENT_INDUSTRY_PACK_TRANSITION_INVALID", str(exc)) from exc

    report = await validate_content_industry_pack(db, user, record.id, commit=False)
    if payload.target_status in {"validated", "canary", "published"} and not (
        report["validation"]["valid"] and report["evaluation"]["passed"]
    ):
        raise _content_error(
            409,
            "CONTENT_INDUSTRY_PACK_VALIDATION_FAILED",
            "行业包校验或离线评测未通过",
            report=report,
        )
    if payload.target_status == "published":
        regression = report.get("regression") or {}
        if (
            regression.get("pack_version_id") != record.id
            or regression.get("pack_hash") != report.get("pack_hash")
            or not regression.get("passed")
        ):
            raise _content_error(
                409,
                "CONTENT_INDUSTRY_PACK_REGRESSION_REQUIRED",
                "Industry Pack 发布前必须完成并通过真实 canary 全链路回归",
                regression=regression,
            )

    previous = None
    if payload.target_status == "published":
        candidate = await repo.get_published_industry_pack_for_update(record.slug, exclude_id=record.id)
        if candidate is not None and candidate.version >= 3:
            candidate.status = "deprecated"
            previous = candidate
        record.rollback_target_version_id = previous.id if previous else record.rollback_target_version_id
        record.published_at = utc_now_naive()
    record.status = payload.target_status
    await repo.track(
        "content_industry_pack_transitioned",
        uid=str(user.uid),
        properties={
            "version_id": record.id,
            "slug": record.slug,
            "target_status": payload.target_status,
            "previous_version_id": previous.id if previous else None,
            "note": payload.note,
        },
    )
    await db.commit()
    return {
        "version": {
            "id": record.id,
            "slug": record.slug,
            "version": record.version,
            "status": record.status,
            "rollback_target_version_id": record.rollback_target_version_id,
            "published_at": format_utc_datetime(record.published_at),
        },
        "report": report,
    }


async def submit_content_industry_pack_regression(
    db: AsyncSession,
    user: User,
    version_id: str,
    payload: IndustryPackRegressionSubmission,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    record = await repo.get_industry_pack_for_update(version_id)
    if record is None or record.tenant_id is not None:
        raise _content_error(404, "CONTENT_INDUSTRY_PACK_MISSING", "平台行业包版本不存在")
    if record.status != "canary":
        raise _content_error(
            409,
            "CONTENT_INDUSTRY_PACK_CANARY_REQUIRED",
            "只能为 canary 状态的 Industry Pack 提交全链路回归结果",
        )

    structural = await validate_content_industry_pack(db, user, version_id, commit=False)
    if not (structural["validation"]["valid"] and structural["evaluation"]["passed"]):
        raise _content_error(
            409,
            "CONTENT_INDUSTRY_PACK_VALIDATION_FAILED",
            "行业包结构校验或离线样本评测未通过",
            report=structural,
        )
    if len(set(payload.source_run_ids)) != len(payload.source_run_ids):
        raise _content_error(422, "CONTENT_INDUSTRY_PACK_RUN_IDS_DUPLICATED", "source_run_ids 不能重复")
    if payload.sample_count != len(payload.source_run_ids):
        raise _content_error(
            422,
            "CONTENT_INDUSTRY_PACK_SAMPLE_COUNT_MISMATCH",
            "sample_count 必须与可审计的 source_run_ids 数量一致",
        )
    canary_runs = await repo.list_industry_pack_canary_runs(record.id, payload.source_run_ids)
    found_run_ids = {item["run_id"] for item in canary_runs}
    missing_run_ids = sorted(set(payload.source_run_ids) - found_run_ids)
    if missing_run_ids:
        raise _content_error(
            422,
            "CONTENT_INDUSTRY_PACK_RUN_INVALID",
            "回归报告包含不属于当前 Industry Pack 的 Run",
            run_ids=missing_run_ids,
        )
    incomplete_run_ids = sorted(item["run_id"] for item in canary_runs if item["status"] != "completed")
    if incomplete_run_ids:
        raise _content_error(
            409,
            "CONTENT_INDUSTRY_PACK_RUN_INCOMPLETE",
            "只能使用已完成的 canary Run 生成回归报告",
            run_ids=incomplete_run_ids,
        )
    covered_directions = {item["content_type_code"] for item in canary_runs}
    missing_directions = sorted(CONTENT_TYPE_CODES - covered_directions)
    if missing_directions:
        raise _content_error(
            409,
            "CONTENT_INDUSTRY_PACK_CANARY_COVERAGE_INCOMPLETE",
            "canary 回归必须覆盖 CT01～CT07 全部内容方向",
            missing_content_type_codes=missing_directions,
        )

    regression = EvaluateIndustryPackRegressionHandler().execute(
        pack=structural["pack"],
        metrics=payload.metrics,
        source_run_ids=payload.source_run_ids,
        sample_count=payload.sample_count,
        candidate_recommendations=payload.candidate_recommendations,
    )
    regression["submitted_at"] = format_utc_datetime(utc_now_naive())
    regression["submitted_by"] = str(user.uid)
    regression["note"] = payload.note
    record.evaluation_report = {**structural, "regression": regression}
    await repo.track(
        "content_industry_pack_regression_submitted",
        uid=str(user.uid),
        properties={
            "version_id": record.id,
            "slug": record.slug,
            "sample_count": payload.sample_count,
            "source_run_ids": payload.source_run_ids,
            "passed": regression["passed"],
            "failed_gates": regression["failed_gates"],
        },
    )
    await db.commit()
    return {"report": record.evaluation_report}


async def update_content_artifact(
    db: AsyncSession, user: User, artifact_id: str, payload: ContentArtifactUpdate
) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    task = await repo.get_task_for_user(artifact.task_id, user, for_update=True)
    _require_v3_task(task)
    artifact.title = payload.title.strip()
    artifact.body = payload.body.strip()
    artifact.topics = payload.topics
    artifact.current_version += 1
    artifact.status = "draft"
    artifact.review_snapshot = {"status": "pending", "checks": []}
    artifact.edit_diff_snapshot = []
    artifact.updated_at = utc_now_naive()
    task.status = "review_required"
    task.current_stage = "review"
    task.review_json = artifact.review_snapshot
    await repo.save_artifact_version(
        artifact=artifact,
        source_type="manual_edit",
        model_spec=None,
        skill_versions=SKILL_VERSIONS,
        rule_version_id=task.rule_version_id,
        knowledge_snapshot=task.evidence_json or {},
        review_snapshot=artifact.review_snapshot,
        created_by=str(user.uid),
    )
    await db.commit()
    return {"artifact": artifact.to_dict()}


async def ai_edit_content_artifact(
    db: AsyncSession,
    user: User,
    artifact_id: str,
    payload: ContentArtifactAIEdit,
) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    task = await repo.get_task_for_user(artifact.task_id, user)
    _require_v3_task(task)
    if not task.latest_run_id:
        raise _content_error(409, "CONTENT_AI_EDIT_WORKFLOW_INCOMPLETE", "内容工作流尚未执行完成")
    run = await AgentRunRepository(db).get_run(task.latest_run_id)
    if run is None or run.status != "completed":
        raise _content_error(409, "CONTENT_AI_EDIT_WORKFLOW_INCOMPLETE", "内容工作流完成后才能使用 AI 修改")
    if artifact.current_version != payload.expected_version:
        raise _content_error(
            409,
            "CONTENT_ARTIFACT_VERSION_CONFLICT",
            "内容版本已更新，请刷新后重新提交修改要求",
            current_version=artifact.current_version,
        )

    model_spec = _validate_model_spec(payload.model_spec)
    source_version = artifact.current_version
    source_title = artifact.title
    source_body = artifact.body
    source_topics = list(artifact.topics or [])
    strategy_snapshot = deepcopy(artifact.strategy_snapshot or {})
    evidence_snapshot = deepcopy(artifact.evidence_snapshot or {})
    validation_strategy = {
        "methods": strategy_snapshot.get("creation_methods") or [],
        "title_formula_code": (strategy_snapshot.get("title_formula") or {}).get("code"),
        "body_formula_code": (strategy_snapshot.get("body_formula") or {}).get("code"),
    }
    latest_run_id = task.latest_run_id
    refined = await refine_generated_content(
        model_spec=model_spec,
        instruction=payload.instruction,
        title=source_title,
        body=source_body,
        topics=source_topics,
        brief=deepcopy(task.brief_json or {}),
        strategy=strategy_snapshot,
        evidence_bundle=evidence_snapshot,
    )
    refined["topics"] = _clean_list(refined["topics"])
    validation = validate_content(
        title=refined["title"],
        body=refined["body"],
        topics=refined["topics"],
        brief=task.brief_json or {},
        evidence_bundle=evidence_snapshot,
        strategy=validation_strategy,
    )
    if validation["status"] == "blocked":
        raise _content_error(
            422,
            "CONTENT_AI_EDIT_VALIDATION_FAILED",
            "AI 修改结果未通过内容校验，原版本未发生变化",
            checks=validation["checks"],
        )

    changes = []
    for field, before in (("title", source_title), ("body", source_body), ("topics", source_topics)):
        after = refined[field]
        if before != after:
            changes.append({"field": field, "before": before, "after": after})
    if not changes:
        raise _content_error(422, "CONTENT_AI_EDIT_NO_CHANGES", "AI 未产生可保存的内容修改")

    locked_artifact = await repo.get_artifact_for_user(artifact_id, user, for_update=True)
    if locked_artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    await db.refresh(locked_artifact)
    locked_task = await repo.get_task_for_user(locked_artifact.task_id, user, for_update=True)
    _require_v3_task(locked_task)
    if locked_artifact.current_version != source_version or locked_task.latest_run_id != latest_run_id:
        raise _content_error(
            409,
            "CONTENT_ARTIFACT_VERSION_CONFLICT",
            "内容或工作流状态已更新，请刷新后重新提交修改要求",
            current_version=locked_artifact.current_version,
        )

    changed_fields = [item["field"] for item in changes]
    field_labels = {"title": "标题", "body": "正文", "topics": "话题"}
    reply = f"已按要求修改{'、'.join(field_labels[field] for field in changed_fields)}，生成新版本。"
    locked_artifact.title = refined["title"]
    locked_artifact.body = refined["body"]
    locked_artifact.topics = refined["topics"]
    locked_artifact.current_version += 1
    locked_artifact.status = "draft"
    locked_artifact.review_snapshot = {"status": "pending", "checks": []}
    locked_artifact.edit_diff_snapshot = [
        {
            "type": "ai_edit",
            "instruction": payload.instruction,
            "reply": reply,
            "changed_fields": changed_fields,
        },
        *changes,
    ]
    locked_artifact.updated_at = utc_now_naive()
    locked_task.status = "review_required"
    locked_task.current_stage = "review"
    locked_task.review_json = locked_artifact.review_snapshot
    await repo.save_artifact_version(
        artifact=locked_artifact,
        source_type="ai_edit",
        model_spec=model_spec,
        skill_versions={},
        rule_version_id=locked_task.rule_version_id,
        knowledge_snapshot=locked_task.evidence_json or {},
        review_snapshot=locked_artifact.review_snapshot,
        created_by=str(user.uid),
    )
    await repo.track(
        "content_artifact_ai_edited",
        uid=str(user.uid),
        task_id=locked_task.id,
        run_id=latest_run_id,
        properties={
            "artifact_id": locked_artifact.id,
            "version": locked_artifact.current_version,
            "changed_fields": changed_fields,
            "model_spec": model_spec,
        },
    )
    await db.commit()
    return {
        "artifact": locked_artifact.to_dict(),
        "reply": reply,
        "changed_fields": changed_fields,
        "validation": validation,
    }


def _merge_reviews(deterministic: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    checks = list(deterministic.get("checks") or []) + list(llm.get("checks") or [])
    status = "blocked" if any(item.get("level") == "error" for item in checks) else "warning" if checks else "passed"
    return {"status": status, "checks": checks}


async def review_content_artifact(
    db: AsyncSession, user: User, artifact_id: str, *, model_spec: str | None
) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    task = await repo.get_task_for_user(artifact.task_id, user, for_update=True)
    _require_v3_task(task)
    deterministic = validate_content(
        title=artifact.title,
        body=artifact.body,
        topics=artifact.topics or [],
        brief=task.brief_json or {},
        evidence_bundle=task.evidence_json or {},
        strategy=task.strategy_json or {},
    )
    llm = await review_generated_content(
        model_spec=_validate_model_spec(model_spec),
        title=artifact.title,
        body=artifact.body,
        topics=artifact.topics or [],
        brief=task.brief_json or {},
        workflow_snapshot=task.strategy_json or {},
        evidence_bundle=task.evidence_json or {},
    )
    review = _merge_reviews(deterministic, llm)
    artifact.review_snapshot = review
    artifact.status = "reviewed" if review["status"] != "blocked" else "blocked"
    task.review_json = review
    task.status = "reviewed" if review["status"] != "blocked" else "review_blocked"
    version_result = await db.execute(
        select(ContentArtifactVersion).where(
            ContentArtifactVersion.artifact_id == artifact.id,
            ContentArtifactVersion.version == artifact.current_version,
        )
    )
    version = version_result.scalar_one_or_none()
    if version:
        version.review_snapshot = review
        await repo.add_review_record(
            artifact_version_id=version.id,
            review_type="combined",
            status=review["status"],
            checks=review["checks"],
            reviewer_uid=str(user.uid),
        )
    await db.commit()
    return {"artifact": artifact.to_dict(), "review": review}


async def finalize_content_artifact(db: AsyncSession, user: User, artifact_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    if (artifact.review_snapshot or {}).get("status") == "blocked":
        raise _content_error(409, "CONTENT_REVIEW_BLOCKED", "内容存在阻断问题，不能保存为正式版本")
    if (artifact.review_snapshot or {}).get("status") not in {"passed", "warning"}:
        raise _content_error(409, "CONTENT_REVIEW_REQUIRED", "请先完成内容审核")
    task = await repo.get_task_for_user(artifact.task_id, user, for_update=True)
    _require_v3_task(task)
    artifact.status = "final"
    artifact.updated_at = utc_now_naive()
    task.status = "completed"
    task.current_stage = "review"
    await repo.track(
        "content_artifact_finalized",
        uid=str(user.uid),
        task_id=task.id,
        properties={"artifact_id": artifact.id, "version": artifact.current_version},
    )
    await db.commit()
    return {"artifact": artifact.to_dict(), "task": task.to_dict()}


async def list_content_artifact_versions(db: AsyncSession, user: User, artifact_id: str) -> dict[str, Any]:
    repo = ContentRepository(db)
    artifact = await repo.get_artifact_for_user(artifact_id, user)
    if artifact is None:
        raise _content_error(404, "CONTENT_ARTIFACT_NOT_FOUND", "内容资产不存在")
    return {"items": await repo.list_artifact_versions(artifact.id)}
