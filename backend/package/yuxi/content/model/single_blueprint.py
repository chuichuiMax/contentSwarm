"""单蓝图创作的通用引用、输入投影与块组装；写作规则由 Skill 维护。"""

import re
from copy import deepcopy

from yuxi.content.model.locked_blocks import extract_locked_quote_block, quote_body_limits


def title_publication_year(pack: dict) -> str | None:
    """标题年份来自本次冻结时间，不作为价格或正文业务数字的来源。"""
    policy = (pack.get("content_rule_bundle") or {}).get("single_blueprint") or {}
    content_type = (pack.get("strategy_snapshot") or {}).get("content_direction")
    if policy.get("title_styles", {}).get(content_type) == "local_labor_standard":
        return str(pack["frozen_at"])[:4]
    return None


def _writing_materials(pack: dict) -> list[dict]:
    """只投影门禁绑定的事实及其授权用途，规则和风格资料不升级为事实。"""
    report = pack.get("material_quality_report")
    requirements = {r["requirement_id"]: r for r in pack.get("material_manifest", {}).get("requirements", [])}
    result = []
    for material in pack["materials"]:
        if material["material_type"] not in {"business_fact", "price_fact", "media_fact"}:
            continue
        if "quote_block" in material["variable_codes"]:
            continue
        codes, usages = set(material["variable_codes"]), set(material["governance"]["allowed_usage"])
        if report is not None:
            bound = [
                requirements[b["requirement_id"]] for b in report["bindings"] if material["id"] in b["material_ids"]
            ]
            codes &= {r["variable_code"] for r in bound}
            usages &= {u for r in bound for u in r["allowed_usage"]}
        if not codes or not usages & {"title", "body"}:
            continue
        result.append(
            {
                **material,
                "variable_codes": sorted(codes),
                "governance": {**material["governance"], "allowed_usage": sorted(usages)},
            }
        )
    return result


def repair_scope(payload: dict) -> dict:
    previous = (payload.get("content_draft") or {}).get("blueprint_content")
    if not previous:
        return {}
    text_ids = {b["id"] for b in previous["blocks"] if b["kind"] == "text"}
    fields, blocks, issues = set(), set(), []
    structural = False
    for report in (payload.get("validation_report"), payload.get("review_report")):
        for issue in (report or {}).get("checks", []):
            if issue.get("status") != "blocked" and issue.get("level") != "error":
                continue
            issues.append(issue)
            location = issue.get("location", "content")
            if location in {"title", "topics"}:
                fields.add(location)
            elif location in text_ids:
                blocks.add(location)
            elif location in {"content", "body"}:
                blocks.update(text_ids)
                structural |= issue["code"] in {
                    "COMPOSITION_ALIGNMENT",
                    "CREATION_TYPE_ALIGNMENT",
                    "NATURAL_EXPRESSION",
                    "BODY_VALUE",
                    "CHANNEL_BODY_SHORT",
                    "BODY_LENGTH_OUT_OF_RANGE",
                }
            else:
                raise ValueError(f"回修位置不是有效内容块: {location}")
    if not issues:
        raise ValueError("回修缺少可定位的问题")
    if blocks and payload["production_pack"]["content_rule_bundle"]["single_blueprint"].get("full_context_repair"):
        blocks = text_ids
        structural = True
    return dict(
        previous=previous,
        editable_fields=sorted(fields),
        editable_blocks=sorted(blocks),
        structure_editable=structural,
        issues=issues,
    )


def project_input(payload: dict, *, review: bool = False) -> dict:
    from yuxi.content.control.workflow.deterministic_node import _required_title_fact_options

    pack = payload["production_pack"]
    policy = pack["content_rule_bundle"]["single_blueprint"]
    quote = extract_locked_quote_block(pack)
    facts = []
    for material in _writing_materials(pack):
        facts.append(
            dict(
                id=material["evidence_ids"][0],
                variables=material["variable_codes"],
                value=material["payload"].get("value", material["payload"].get("quoted_value")),
                unit=material["payload"].get("unit"),
                allowed_usage=material["governance"]["allowed_usage"],
                source_type=material["source"].get("source_type"),
                source=deepcopy(material["source"]),
            )
        )
    # 兼容旧物料的能力聚合字段：不将它作为已发生施工过程供写作使用。
    for fact in facts:
        if fact["variables"] == ["process"] and "工种能力：" in str(fact["value"]):
            fact["variables"] = ["capability_description"]
        if "persona_fact" in fact["variables"] and isinstance(fact["value"], str):
            fact["value"] = re.sub(r"沟通语气：[^。]*。?", "", fact["value"])
    structured = (payload.get("content_brief", {}).get("persona") or {}).get("structured")
    for fact in facts:
        fact["role"] = (
            "reader_context"
            if set(fact["variables"]) & {"audience", "pain", "pain_points"} and structured
            else "project_context"
            if set(fact["variables"]) & {"project_site", "case_background", "scene", "quantity", "product"}
            else "business_fact"
        )
        codes = set(fact["variables"])
        fact["category"] = (
            "identity"
            if "persona_fact" in codes
            else "capability"
            if "capability_description" in codes
            else "service_feature"
            if codes & {"advantages", "advantage"}
            else "work_record"
            if codes & {"process", "case_result"}
            else fact["role"]
        )
    if structured:
        for fact in facts:
            if "persona_fact" in fact["variables"]:
                fact["value"] = {
                    k: v
                    for k, v in structured.items()
                    if k not in {"tone", "serviceAdvantages"} and v not in (None, "", [], {})
                }
                fact["unit"] = None
                # 接口 skills 可能包含身份，保留原始字段来源但不把角色称为工序。
                fact["value"]["roles"] = [s for s in structured.get("skills", []) if s in {"工长", "项目经理", "监理"}]
                if policy.get("writing_mode") != "direct_reference":
                    fact["value"].pop("skills", None)
            if fact["variables"] == ["capability_description"]:
                fact["value"] = {
                    "skills": [s for s in structured.get("skills", []) if s not in {"工长", "项目经理", "监理"}],
                    "service_features": structured.get("serviceAdvantages", []),
                }
    aliases = {f["id"]: f"F{i}" for i, f in enumerate(facts, 1)}
    for f in facts:
        f["id"] = aliases[f["id"]]
    reference = pack["reference_snapshot"]
    blueprint = deepcopy(reference["reference_blueprint"])
    card = reference.get("reference_card") or {}
    sequence = blueprint.pop("content_block_sequence")
    blocks = {f"R{i}": text for i, text in enumerate(sequence, 1)}
    mappings = {}
    evidence = payload["evidence_bundle"]["items"]
    for slot, paths in reference.get("slot_mapping", {}).items():
        ids = []
        for path in paths:
            parts = path.split(".")
            if parts[:2] == ["evidence_bundle", "items"]:
                eid = evidence[int(parts[2])]["id"]
                if eid in aliases:
                    ids.append(aliases[eid])
                elif quote and eid in quote["evidence_ids"]:
                    ids.append("quote_block")
            else:
                ids.extend(f["id"] for f in facts if parts[-1] in f["variables"])
        mappings[slot] = list(dict.fromkeys(ids))
    title_options = _required_title_fact_options(payload.get("content_brief") or {}, pack["strategy_snapshot"], pack)
    runtime = pack["content_rule_bundle"]["runtime_rules"]
    brief = payload.get("content_brief") or {}
    conflict = set(brief.get("required_terms") or []) & set(brief.get("forbidden_terms") or [])
    if conflict:
        raise ValueError("要求词与禁用词冲突：" + "、".join(sorted(conflict)))
    result = dict(
        stage="review" if review else "write",
        content_type=pack["strategy_snapshot"]["content_direction"],
        writing_requirements=dict(
            request=pack.get("writing_request") or "",
            required_terms=list((payload.get("content_brief") or {}).get("required_terms") or []),
            forbidden_terms=list((payload.get("content_brief") or {}).get("forbidden_terms") or []),
        ),
        title_requirements=title_options,
        facts=facts,
        reference=dict(
            id=reference.get("id"),
            source_hash=reference.get("source_hash"),
            title=reference.get("title", ""),
            body=reference.get("body", ""),
            blocks=blocks,
            style=blueprint,
            slot_mapping=mappings,
            expression_anchors=deepcopy(card.get("anchors") or []),
            slot_candidates={
                slot["slot_key"]: {
                    # 提取器的 description 可能含“改写必须”，这里只传原文作用名称。
                    "purpose": slot.get("name") or slot["slot_key"],
                    "reference_quote": (slot.get("anchor") or {}).get("quote", ""),
                    "candidate_facts": mappings.get(slot["slot_key"], []),
                    "support_status": "unassessed",
                }
                for slot in card.get("required_slots") or []
            },
        ),
        requirements=policy["requirements"].get(pack["strategy_snapshot"]["content_direction"], []),
        title_limits=pack["channel_profile"]["title_constraints"],
        body_limits=dict(
            min_chars=policy["creative_min_chars"],
            max_chars=policy.get("creative_max_chars", 650)
            or pack["channel_profile"]["body_constraints"].get("max_length", 1000),
        ),
        layout=policy.get("layout", {}),
        emoji_allowed=pack["channel_profile"]["body_constraints"].get("emoji_allowed", True),
        platform_rules={
            "forbidden_replacements": runtime["viral-platform-expression"].get("forbidden_replacements", {}),
            "forbidden_alternatives": runtime["viral-platform-expression"]
            .get("forbidden_lexicon", {})
            .get("alternatives", {}),
            "lexicon_snapshot_hash": runtime["viral-platform-expression"]
            .get("forbidden_lexicon", {})
            .get("snapshot_hash"),
            "forbidden_direct_cta_examples": runtime["viral-platform-expression"].get("direct_cta_patterns", []),
            "unsupported_promise_examples": runtime["viral-platform-expression"].get("high_risk_claims", []),
        },
        topics=dict(candidates=pack["content_rule_bundle"]["topic_candidates"], **runtime["viral-topic-author"]),
        tone=(payload.get("content_brief", {}).get("persona") or {}).get("tone"),
        quote=None,
    )
    if year := title_publication_year(pack):
        result["title_guidance"] = {
            "style": "城市装修、发布年份、工费标准或工价明细；不以单项每平方米价格为标题卖点",
            "publication_year": year,
            "year_source": "production_pack.frozen_at",
            "scope": "按报价实际覆盖的工种命名；只有拆除报价时不写各工种",
        }
    if quote and brief.get("business_variables", {}).get("external_source") in {"dangjia", "content_studio_case"}:
        # 结构化接口的“单价面积”等是报价格式，不是用户要求讲解计价方法。
        result["writing_requirements"]["request"] = "使用本篇项目资料与真实报价，仿写所选参考，创作装修报价笔记。"
    if policy.get("compact_reference"):
        # 完整原文已提供表达上下文，不再将多份提取摘要重复作为写作提示。
        result["reference"] = {
            key: result["reference"][key] for key in ("id", "source_hash", "title", "body", "blocks")
        }
    if policy.get("narrative_policy"):
        result["narrative_policy"] = policy["narrative_policy"]
    if quote:
        result["quote"] = dict(
            id="quote_block",
            context=quote["rendered_content"],
            output_block={"id": "quote_block", "kind": "quote_ref", "text": "", "facts": [], "blueprint_refs": []},
        )
        result["body_limits"]["max_chars"] = quote_body_limits(pack, quote["rendered_content"])[
            "creative_body_max_chars"
        ]
    if policy.get("writing_mode") == "direct_reference":
        result["facts"] = [
            {k: fact[k] for k in ("id", "variables", "value", "unit", "allowed_usage")} for fact in facts
        ]
        result["reference"] = {k: result["reference"][k] for k in ("id", "title", "body")}
        result["reference"]["blocks"] = {}
        result["topics"] = {"max_count": pack["channel_profile"].get("topic_constraints", {}).get("max_count", 10)}
        result["writing_requirements"]["tags"] = brief.get("business_variables", {}).get("content_tags", [])
        for key in ("requirements", "narrative_policy", "layout"):
            result.pop(key, None)
        for key in ("forbidden_direct_cta_examples", "unsupported_promise_examples"):
            result["platform_rules"].pop(key, None)
    # 原文锚点属于唯一参考的表达证据，不能进入 facts。
    if review:
        draft = deepcopy(payload["content_draft"]["blueprint_content"])
        for b in draft["blocks"]:
            if b["kind"] == "quote_ref":
                b["text"] = quote["rendered_content"]
        if result["quote"]:
            result["quote"] = {"id": "quote_block", "context": quote["rendered_content"], "read_only": True}
        result.update(
            draft=draft,
            deterministic_checks=payload["validation_report"],
            required_review_codes=payload["required_review_codes"],
        )
    elif scope := repair_scope(payload):
        result.update(stage="repair", **{k: v for k, v in scope.items() if k != "previous"})
        result["current"] = scope["previous"]
        if policy.get("full_context_repair"):
            return result
        if scope["editable_fields"] == ["title"] and not scope["editable_blocks"]:
            result = {
                k: result[k]
                for k in [
                    "stage",
                    "writing_requirements",
                    "title_requirements",
                    "title_limits",
                    "platform_rules",
                    "editable_fields",
                    "editable_blocks",
                    "structure_editable",
                    "issues",
                ]
            }
            result["current_title"] = scope["previous"]["title"]
            result["facts"] = [f for f in facts if "title" in f["allowed_usage"]]
        elif scope["editable_fields"] == ["topics"] and not scope["editable_blocks"]:
            result = {
                k: result[k]
                for k in [
                    "stage",
                    "writing_requirements",
                    "topics",
                    "editable_fields",
                    "editable_blocks",
                    "structure_editable",
                    "issues",
                ]
            }
            result["current_title"] = scope["previous"]["title"]
            result["current_topics"] = scope["previous"]["topics"]
        elif not scope["structure_editable"]:
            current = scope["previous"]["blocks"]
            indices = {i for i, b in enumerate(current) if b["id"] in scope["editable_blocks"]}
            visible = {j for i in indices for j in (i - 1, i, i + 1) if 0 <= j < len(current)}
            result["current"] = {**scope["previous"], "blocks": [b for i, b in enumerate(current) if i in visible]}
            refs = {ref for b in result["current"]["blocks"] for ref in b["blueprint_refs"]}
            result["reference"]["blocks"] = {k: v for k, v in blocks.items() if k in refs}
            if "topics" not in scope["editable_fields"]:
                result.pop("topics")
                result["current"].pop("topics")
            related_facts = {fid for block in result["current"]["blocks"] for fid in block["facts"]}
            related_facts.update(scope["previous"]["title"]["facts"])
            if any(issue["code"].startswith("PERSONA_") for issue in scope["issues"]):
                related_facts.update(
                    f["id"]
                    for f in facts
                    if set(f["variables"]) & {"persona_fact", "advantages", "capability_description"}
                )
            result["facts"] = [f for f in facts if f["id"] in related_facts]
            result["reference"]["slot_mapping"] = {
                key: [fid for fid in ids if fid in related_facts or fid == "quote_block"]
                for key, ids in mappings.items()
                if any(fid in related_facts for fid in ids)
            }
            if "title" not in scope["editable_fields"]:
                result.pop("title_requirements")
                result.pop("title_limits")
    return result


def validate_and_assemble(result: dict, payload: dict, *, patch: bool = False) -> dict:
    """验证引用与修改范围，转换为旧下游可读取的标题/正文结构。"""
    pack = payload["production_pack"]
    full_view = project_input({**payload, "content_draft": None})
    if patch:
        scope = repair_scope(payload)
        full = deepcopy(scope["previous"])
        for key in ("title", "topics"):
            if result[key] is not None:
                if key not in scope["editable_fields"] and result[key] != full[key]:
                    raise ValueError(f"不允许修改 {key}")
                full[key] = result[key]
        if scope["structure_editable"]:
            if result.get("writing_design") is not None:
                full["writing_design"] = result["writing_design"]
            full["blocks"] = result["blocks"]
            full["omissions"] = result["omissions"] if result["omissions"] is not None else full["omissions"]
        else:
            if result["omissions"] is not None:
                raise ValueError("局部回修不允许修改省略记录")
            updates = {b["id"]: b for b in result["blocks"]}
            if len(updates) != len(result["blocks"]) or not set(updates) <= set(scope["editable_blocks"]):
                raise ValueError("回修包含重复或未授权块")
            for old in full["blocks"]:
                if old["id"] in updates:
                    new = updates[old["id"]]
                    if any(new[k] != old[k] for k in ("kind", "blueprint_refs")):
                        raise ValueError("局部回修不能修改结构元数据")
                    old.update(new)
        result = full
    if result.get("writing_design") is None:
        # 新构思元数据不改变历史正文结构，也不进入可见文章。
        result = {key: value for key, value in result.items() if key != "writing_design"}
    if pack["content_rule_bundle"]["single_blueprint"].get("full_context_repair"):
        result = deepcopy(result)
        for block in result["blocks"]:
            if block["kind"] == "quote_ref":
                # 报价的内部标识与证据归属由程序维护，模型只决定其位置。
                block["id"] = "quote_block"
    missing = {
        slot: options
        for slot, options in full_view["title_requirements"].items()
        if not any(option.replace(",", "") in result["title"]["text"].replace(",", "") for option in options)
    }
    contract_issues = []
    if missing:
        contract_issues.append(f"标题每个必需槽位须原样包含至少一个候选，缺少：{missing}")
    if any(b["kind"] == "quote_ref" and (b["id"] != "quote_block" or b["text"]) for b in result["blocks"]):
        contract_issues.append("报价块 id 必须为 quote_block 且 text 为空，原文由程序插入")
    if contract_issues:
        raise ValueError("；".join(contract_issues))
    quote = extract_locked_quote_block(pack)
    ids = [b["id"] for b in result["blocks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("块 ID 重复")
    quote_blocks = [b for b in result["blocks"] if b["kind"] == "quote_ref"]
    if len(quote_blocks) != int(quote is not None):
        raise ValueError("报价引用数量不正确")
    if not any(b["kind"] == "text" and b["text"].strip() for b in result["blocks"]):
        raise ValueError("正文必须包含实际创作的文本，不能只提交报价引用")
    facts = {f["id"]: f for f in full_view["facts"]}
    for usage, references in [
        ("title", result["title"]["facts"]),
        *[("body", b["facts"]) for b in result["blocks"] if b["kind"] == "text"],
    ]:
        if any(i not in facts or usage not in facts[i]["allowed_usage"] for i in references):
            raise ValueError(f"{usage} 使用了未知或未授权事实")
    adopted = set()
    for b in result["blocks"]:
        if b["kind"] == "quote_ref":
            # 报价证据归属由冻结报价决定，模型不承担抄写其来源的职责。
            b["facts"] = []
        elif not b["text"].strip():
            raise ValueError("文本块不能为空")
        elif quote and (quote["rendered_content"] in b["text"] or quote["original_content"] in b["text"]):
            raise ValueError("原创块重复了冻结报价")
        adopted.update(b["blueprint_refs"])
    omitted = {o["ref"] for o in result["omissions"]}
    expected = set(full_view["reference"]["blocks"])
    if expected and not adopted:
        raise ValueError("仿写必须记录实际采用的蓝图作用，不能提交空采用记录")
    if len(omitted) != len(result["omissions"]) or not adopted | omitted <= expected:
        raise ValueError(
            f"蓝图编号只能用 {sorted(expected)}，省略记录不能重复；未知={sorted((adopted | omitted) - expected)}"
        )
    original_facts = [m["evidence_ids"][0] for m in _writing_materials(pack)]
    fact_ids = {f"F{i}": eid for i, eid in enumerate(original_facts, 1)}
    body_code = pack["strategy_snapshot"]["body_formula"]["code"]
    title = dict(
        text=result["title"]["text"],
        formula_code=pack["strategy_snapshot"]["title_formula"]["code"],
        evidence_ids=[fact_ids[i] for i in result["title"]["facts"]],
        lexicon_usage=[],
    )
    text_blocks = [b for b in result["blocks"] if b["kind"] == "text"]
    return dict(
        title=title,
        outline=dict(body_formula_code=body_code, sections=[]),
        draft=dict(
            body="\n\n".join(b["text"] for b in text_blocks),
            topics=result["topics"],
            body_formula_code=body_code,
            lexicon_usage=[],
            blueprint_content=result,
            paragraph_evidence=[
                dict(paragraph_id=b["id"], evidence_ids=[fact_ids[i] for i in b["facts"]]) for b in text_blocks
            ],
        ),
    )
