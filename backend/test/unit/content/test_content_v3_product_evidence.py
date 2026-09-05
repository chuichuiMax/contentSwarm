from __future__ import annotations

import hashlib
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from yuxi.agents.buildin.content_workflow.graph import ContentWorkflowAgent
from yuxi.agents.buildin.content_workflow.state import ContentWorkflowState
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.contracts import ContractDomainContext, ContractDomainValidationError
from yuxi.content.model.contracts.content_nodes import validate_content_node_result
from yuxi.content.model.evidence import EvidenceGovernanceError, EvidenceItemV1, freeze_evidence_bundle
from yuxi.services.agent_delegation_service import AgentDelegationRequest, AgentDelegationService


def _strategy_snapshot() -> dict:
    payload = {
        "content_direction": "CT01",
        "selected_group_id": "group-1",
        "creation_methods": ["M01", "M03"],
        "creation_method_definitions": [
            {
                "code": "M01",
                "name": "数字法",
                "variable_schema": ["number", "result"],
            },
            {
                "code": "M03",
                "name": "价值法",
                "variable_schema": ["advantages"],
            },
        ],
        "title_formula": {"code": "T01", "variable_schema": ["audience", "number", "result"]},
        "body_formula": {"code": "C02", "required_variables": ["product", "result"]},
        "rule_version_id": "rules-v3",
        "match_snapshot_id": "match-1",
        "formula_snapshot_id": "formula-1",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["snapshot_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _domain_context(evidence_bundle: dict, *, slots: tuple[str, ...] = ()) -> ContractDomainContext:
    return ContractDomainContext.from_governance(
        match_decision_snapshot={"selected_group_id": "group-1", "eligible_title_formula_codes": ["T01"]},
        formula_selection_snapshot={
            "selected_title_formula_code": "T01",
            "selected_body_formula_code": "C02",
        },
        evidence_bundle=evidence_bundle,
        locked_versions={
            "industry_pack_version_id": "pack-1",
            "channel_profile_version_id": "channel-1",
            "persona_profile_version_id": None,
            "rule_version_id": "rules-v3",
            "title_formula_code": "T01",
            "body_formula_code": "C02",
            "artifact_version_id": None,
        },
        locked_values={},
        product_material_requirements={
            "requirements": [
                {
                    "requirement_id": slot,
                    "material_type": slot,
                    "target_usages": ["style_reference"] if slot == "viral_example" else ["title", "body"],
                }
                for slot in slots
            ],
        },
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_locked_strategy_resolves_formula_specific_product_material_requirements():
    handler = V3DeterministicNodeHandler()

    result = await handler.execute(
        db=object(),
        node={"id": "resolve_product_material_requirements"},
        state={"strategy_snapshot": _strategy_snapshot()},
        node_run_id="node-1",
    )

    requirements = {item["requirement_id"]: item for item in result["product_material_requirements"]["requirements"]}
    assert requirements["product_profile"]["required"] is True
    assert requirements["case_proof"]["required"] is True
    assert requirements["viral_example"]["target_usages"] == ["style_reference"]
    assert result["product_material_requirements"]["strategy_snapshot_hash"] == _strategy_snapshot()["snapshot_hash"]


@pytest.mark.unit
def test_product_research_contract_enforces_price_and_viral_example_governance():
    bundle = {"items": []}
    context = _domain_context(bundle, slots=("price", "viral_example"))
    base_item = {
        "id": "ev-price",
        "variable_codes": ["price"],
        "value": "套餐报价 12800 元",
        "source_type": "knowledge_base",
        "source_id": "doc-price",
        "source_version": "v1",
        "verified_status": "retrieved",
        "allowed_usage": ["title", "body"],
        "risk_level": "high_risk",
        "source_hash": "a" * 64,
        "metadata": {"material_type": "price"},
    }
    payload = {
        "evidence_items": [base_item],
        "citations": ["doc-price"],
        "slot_mappings": [
            {
                "slot": "price",
                "target_usage": "body",
                "evidence_ids": ["ev-price"],
                "integration_instruction": "在报价段说明适用范围",
            }
        ],
        "unresolved_questions": [],
    }

    result = validate_content_node_result("ProductEvidenceCollectionResultV1", payload, context)
    assert result.evidence_items[0].metadata == {"material_type": "price"}

    payload["evidence_items"][0]["risk_level"] = "normal"
    with pytest.raises(ContractDomainValidationError, match="high_risk"):
        validate_content_node_result("ProductEvidenceCollectionResultV1", payload, context)
    payload["evidence_items"][0]["risk_level"] = "high_risk"

    viral = {
        **base_item,
        "id": "ev-sample",
        "variable_codes": [],
        "value": "爆款标题里的 999 个技巧",
        "risk_level": "normal",
        "metadata": {"material_type": "viral_example", "usage_mode": "structure_reference_only"},
    }
    payload["evidence_items"] = [viral]
    payload["slot_mappings"] = [
        {
            "slot": "viral_example",
            "target_usage": "style_reference",
            "evidence_ids": ["ev-sample"],
            "integration_instruction": "只参考结构",
        }
    ]
    with pytest.raises(ContractDomainValidationError, match="未授权|样式参考"):
        validate_content_node_result("ProductEvidenceCollectionResultV1", payload, context)


@pytest.mark.unit
def test_case_evidence_cannot_fill_viral_style_reference_slot():
    context = _domain_context({"items": []}, slots=("case_proof", "viral_example"))
    payload = {
        "evidence_items": [
            {
                "id": "ev_case_89m2_anonymous",
                "variable_codes": ["result", "scene"],
                "value": "89㎡项目增加12㎡收纳空间",
                "source_type": "business_record",
                "source_id": "case-89m2",
                "source_version": "v1",
                "verified_status": "retrieved",
                "allowed_usage": ["style_reference"],
                "risk_level": "sensitive",
                "source_hash": "d" * 64,
                "metadata": {"material_type": "case_proof"},
            }
        ],
        "citations": ["case-89m2"],
        "slot_mappings": [
            {
                "slot": "viral_example",
                "target_usage": "style_reference",
                "evidence_ids": ["ev_case_89m2_anonymous"],
                "integration_instruction": "作为爆款结构参考",
            }
        ],
        "unresolved_questions": [],
    }

    with pytest.raises(ContractDomainValidationError, match="其他资料类型"):
        validate_content_node_result("ProductEvidenceCollectionResultV1", payload, context)


@pytest.mark.unit
def test_product_research_cannot_resubmit_an_existing_evidence_id_as_new():
    existing = EvidenceItemV1(
        id="ev-existing",
        variable_codes=("area",),
        value="89㎡",
        source_type="manual_input",
        source_id="field-area",
        source_version="v1",
        verified_status="user_confirmed",
        allowed_usage=("title", "body"),
        source_hash="a" * 64,
    )
    bundle = freeze_evidence_bundle(task_id="task-1", version=1, items=[existing]).model_dump(mode="json")
    context = _domain_context(bundle, slots=("case_proof",))
    payload = {
        "evidence_items": [
            {
                **existing.model_dump(mode="json", exclude={"created_at"}),
                "source_hash": "b" * 64,
                "metadata": {"material_type": "case_proof"},
            }
        ],
        "citations": [],
        "slot_mappings": [
            {
                "slot": "case_proof",
                "target_usage": "title",
                "evidence_ids": ["ev-existing"],
                "integration_instruction": "引用既有面积证据",
            }
        ],
        "unresolved_questions": [],
    }

    with pytest.raises(ContractDomainValidationError, match="只能在 slot_mappings 中引用"):
        validate_content_node_result("ProductEvidenceCollectionResultV1", payload, context)


@pytest.mark.unit
def test_viral_example_numbers_never_authorize_title_numbers():
    style_item = EvidenceItemV1(
        id="ev-style",
        variable_codes=(),
        value="爆款样例使用 999 形成反差",
        source_type="knowledge_base",
        source_id="sample-doc",
        source_version="v1",
        verified_status="retrieved",
        allowed_usage=("style_reference",),
        source_hash="b" * 64,
        metadata={"material_type": "viral_example", "usage_mode": "structure_reference_only"},
    )
    bundle = freeze_evidence_bundle(task_id="task-1", version=1, items=[style_item]).model_dump(mode="json")

    with pytest.raises(ContractDomainValidationError, match="无证据数字"):
        validate_content_node_result(
            "TitleCandidatesResultV1",
            {
                "candidates": [
                    {"id": "t1", "text": "999 个产品技巧", "formula_code": "T01", "evidence_ids": [], "reason": "结构"},
                    {"id": "t2", "text": "产品技巧 999", "formula_code": "T01", "evidence_ids": [], "reason": "结构"},
                ],
                "selected_title_formula_code": "T01",
                "evidence_ids": [],
            },
            _domain_context(bundle),
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_freeze_product_evidence_blocks_when_required_slot_is_unmapped():
    empty_bundle = freeze_evidence_bundle(task_id="task-1", version=1, items=[]).model_dump(mode="json")
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "evidence_bundle": empty_bundle,
        "product_material_requirements": {
            "strategy_snapshot_hash": _strategy_snapshot()["snapshot_hash"],
            "requirements": [{"requirement_id": "product_profile", "required": True}],
        },
        "product_evidence_collection": {
            "evidence_items": [],
            "citations": [],
            "slot_mappings": [],
            "unresolved_questions": ["缺少正式产品介绍"],
        },
    }

    with pytest.raises(EvidenceGovernanceError, match="product_profile"):
        await V3DeterministicNodeHandler().execute(
            db=object(),
            node={"id": "freeze_product_evidence_bundle"},
            state=state,
            node_run_id="node-1",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_freeze_product_evidence_builds_pack_bound_to_strategy_and_bundle():
    product = EvidenceItemV1(
        id="ev-product",
        variable_codes=("product", "advantages"),
        value="全屋收纳设计服务，适合改善型家庭",
        source_type="knowledge_base",
        source_id="product-doc",
        source_version="v3",
        verified_status="retrieved",
        allowed_usage=("title", "body"),
        source_hash="c" * 64,
        metadata={"material_type": "product_profile"},
    )
    bundle = freeze_evidence_bundle(task_id="task-1", version=2, items=[product]).model_dump(mode="json")
    strategy = _strategy_snapshot()
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "evidence_bundle": bundle,
        "product_material_requirements": {
            "strategy_snapshot_hash": strategy["snapshot_hash"],
            "requirements": [
                {
                    "requirement_id": "product_profile",
                    "required": True,
                    "target_usages": ["body"],
                }
            ],
        },
        "product_evidence_collection": {
            "evidence_items": [],
            "citations": [],
            "slot_mappings": [
                {
                    "slot": "product_profile",
                    "target_usage": "body",
                    "evidence_ids": ["ev-product"],
                    "integration_instruction": "在方案段说明服务和适用人群",
                }
            ],
            "unresolved_questions": [],
        },
    }

    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "freeze_product_evidence_bundle"},
        state=state,
        node_run_id="node-1",
    )

    pack = result["product_evidence_pack"]
    assert pack["strategy_snapshot_hash"] == strategy["snapshot_hash"]
    assert pack["evidence_bundle_hash"] == bundle["bundle_hash"]
    assert pack["slot_mappings"][0]["required"] is True
    assert len(pack["pack_hash"]) == 64


@pytest.mark.unit
@pytest.mark.asyncio
async def test_freeze_product_evidence_uses_frozen_item_when_legacy_result_repeats_its_id():
    product = EvidenceItemV1(
        id="ev-product",
        variable_codes=("product",),
        value="全屋收纳设计服务",
        source_type="manual_input",
        source_id="field-product",
        source_version="v1",
        verified_status="user_confirmed",
        allowed_usage=("body",),
        source_hash="a" * 64,
        metadata={"material_type": "product_profile"},
    )
    bundle = freeze_evidence_bundle(task_id="task-1", version=1, items=[product]).model_dump(mode="json")
    strategy = _strategy_snapshot()
    repeated = product.model_dump(mode="json", exclude={"created_at"})
    repeated["source_hash"] = "b" * 64
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "evidence_bundle": bundle,
        "product_material_requirements": {
            "strategy_snapshot_hash": strategy["snapshot_hash"],
            "requirements": [
                {
                    "requirement_id": "product_profile",
                    "required": True,
                    "target_usages": ["body"],
                }
            ],
        },
        "product_evidence_collection": {
            "evidence_items": [repeated],
            "citations": [],
            "slot_mappings": [
                {
                    "slot": "product_profile",
                    "target_usage": "body",
                    "evidence_ids": ["ev-product"],
                    "integration_instruction": "引用已冻结产品证据",
                }
            ],
            "unresolved_questions": [],
        },
    }

    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "freeze_product_evidence_bundle"},
        state=state,
        node_run_id="node-1",
    )

    assert result["evidence_bundle"] == bundle
    assert result["product_evidence_pack"]["slot_mappings"][0]["evidence_ids"] == ["ev-product"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_title_and_body_validation_require_mapped_product_evidence(monkeypatch):
    handler = V3DeterministicNodeHandler()
    evidence_bundle = {
        "items": [
            {
                "id": "ev-product",
                "value": "全屋收纳设计服务",
                "verified_status": "retrieved",
                "allowed_usage": ["title", "body"],
            }
        ]
    }
    product_pack = {
        "slot_mappings": [
            {
                "slot": "product_profile",
                "target_usage": "title",
                "required": True,
                "evidence_ids": ["ev-product"],
                "integration_instruction": "标题中呈现服务主体",
            },
            {
                "slot": "product_profile",
                "target_usage": "body",
                "required": True,
                "evidence_ids": ["ev-product"],
                "integration_instruction": "正文中呈现服务主体",
            },
            {
                "slot": "brand",
                "target_usage": "title",
                "required": False,
                "evidence_ids": ["ev-product"],
                "integration_instruction": "需要品牌背书时使用",
            },
        ]
    }
    title_result = await handler.execute(
        db=object(),
        node={"id": "validate_title_candidates"},
        state={
            "formula_selection_snapshot": {"selected_title_formula_code": "T01"},
            "evidence_bundle": evidence_bundle,
            "product_evidence_pack": product_pack,
            "title_candidates": [
                {"id": "t1", "text": "适合改善家庭的收纳方案", "formula_code": "T01", "evidence_ids": []},
                {
                    "id": "t2",
                    "text": "改善家庭可以这样规划收纳",
                    "formula_code": "T01",
                    "evidence_ids": ["ev-product"],
                },
            ],
        },
        node_run_id="node-title",
    )
    assert title_result["title_candidates"][0]["selectable"] is False
    assert title_result["title_candidates"][1]["selectable"] is True
    assert title_result["title_validation_report"]["status"] == "passed"
    assert title_result["title_validation_report"]["items"][0]["missing_required_slots"] == ["product_profile"]

    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    body_result = await handler.execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state={
            "selected_title": {"text": "标题"},
            "content_brief": {"brand": {"name": "测试品牌"}},
            "strategy_snapshot": _strategy_snapshot(),
            "evidence_bundle": evidence_bundle,
            "product_evidence_pack": product_pack,
            "content_draft": {"body": "这是一段产品正文。" * 20, "topics": [], "paragraph_evidence": []},
        },
        node_run_id="node-body",
    )
    assert body_result["validation_report"]["status"] == "blocked"
    assert body_result["validation_report"]["checks"][-1]["code"] == "BODY_PRODUCT_EVIDENCE_NOT_USED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_validation_requires_available_business_knowledge_evidence(monkeypatch):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state={
            "selected_title": {"text": "标题"},
            "content_brief": {"brand": {"name": "测试品牌"}},
            "strategy_snapshot": _strategy_snapshot(),
            "evidence_bundle": {
                "items": [
                    {
                        "id": "ev-kb-craft",
                        "source_type": "knowledge_base",
                        "allowed_usage": ["body"],
                        "metadata": {"material_type": "craft"},
                    }
                ]
            },
            "product_evidence_pack": {},
            "content_draft": {"body": "这是一段足够长的正文。" * 20, "topics": [], "paragraph_evidence": []},
        },
        node_run_id="node-body-kb",
    )

    assert result["validation_report"]["status"] == "blocked"
    assert result["validation_report"]["checks"][-1]["code"] == "KNOWLEDGE_EVIDENCE_UNUSED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_validation_blocks_mechanical_outline_narration(monkeypatch):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state={
            "selected_title": {"text": "标题"},
            "content_brief": {"brand": {"name": "测试品牌"}},
            "strategy_snapshot": _strategy_snapshot(),
            "evidence_bundle": {"items": []},
            "product_evidence_pack": {},
            "content_draft": {
                "body": "旧况很典型：这段正文用具体内容补足长度。" * 12,
                "topics": [],
                "paragraph_evidence": [],
            },
        },
        node_run_id="node-body-mechanical",
    )

    assert result["validation_report"]["status"] == "blocked"
    assert result["validation_report"]["checks"][-1]["code"] == "MECHANICAL_META_EXPRESSION"
    assert result["validation_report"]["checks"][-1]["matched_terms"] == ["旧况很典型"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_validation_requires_concrete_value_when_price_evidence_is_cited(monkeypatch):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    state = {
        "selected_title": {"text": "标题"},
        "content_brief": {"brand": {"name": "测试品牌"}},
        "strategy_snapshot": _strategy_snapshot(),
        "evidence_bundle": {
            "items": [
                {
                    "id": "ev-kb-price",
                    "source_type": "knowledge_base",
                    "allowed_usage": ["body"],
                    "metadata": {"material_type": "price"},
                    "value": {
                        "items": [
                            {"sku": "基础定制设计", "price": 42.8, "unit": "元/平方米"},
                            {"sku": "设计上门服务", "price": 400, "unit": "元/项"},
                        ]
                    },
                }
            ]
        },
        "product_evidence_pack": {},
        "content_draft": {
            "body": "知识库价格按平方米或按项计算，具体仍要结合方案范围。" * 10,
            "topics": [],
            "paragraph_evidence": [{"paragraph_id": "p1", "evidence_ids": ["ev-kb-price"]}],
        },
    }

    vague_result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state=state,
        node_run_id="node-body-price-vague",
    )
    assert vague_result["validation_report"]["status"] == "blocked"
    assert vague_result["validation_report"]["checks"][-1]["code"] == "KNOWLEDGE_PRICE_DETAIL_UNUSED"

    state["content_draft"]["body"] = "基础定制设计为42.8元/平方米，适用范围仍需结合具体方案确认。" * 10
    concrete_result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state=state,
        node_run_id="node-body-price-concrete",
    )
    assert concrete_result["validation_report"] == {"status": "passed", "checks": []}

    state["evidence_bundle"]["items"][0]["value"] = "基础定制设计42.8元/平方米"
    text_value_result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state=state,
        node_run_id="node-body-price-text-value",
    )
    assert text_value_result["validation_report"] == {"status": "passed", "checks": []}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_validation_does_not_require_platform_rules_as_article_evidence(monkeypatch):
    monkeypatch.setattr(
        "yuxi.content.control.workflow.deterministic_node.validate_content",
        lambda **kwargs: {"status": "passed", "checks": []},
    )
    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "deterministic_validate"},
        state={
            "selected_title": {"text": "标题"},
            "content_brief": {"brand": {"name": "测试品牌"}},
            "strategy_snapshot": _strategy_snapshot(),
            "evidence_bundle": {
                "items": [
                    {
                        "id": "ev-kb-platform-rule",
                        "source_type": "knowledge_base",
                        "allowed_usage": ["body"],
                        "metadata": {"material_type": "platform_rule"},
                    }
                ]
            },
            "product_evidence_pack": {},
            "content_draft": {"body": "这是一段足够长的正文。" * 20, "topics": [], "paragraph_evidence": []},
        },
        node_run_id="node-body-platform-rule",
    )

    assert result["validation_report"] == {"status": "passed", "checks": []}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_all_blocked_titles_return_detailed_report_for_targeted_regeneration():
    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "validate_title_candidates"},
        state={
            "formula_selection_snapshot": {"selected_title_formula_code": "T02"},
            "strategy_snapshot": {
                "title_formula": {"variable_schema": ["number", "result"]},
                "body_formula": {"required_variables": ["product", "result"]},
                "creation_method_definitions": [],
            },
            "product_material_requirements": {
                "requirements": [
                    {
                        "requirement_id": "case_proof",
                        "required": True,
                        "variable_codes": ["number", "result"],
                    },
                    {
                        "requirement_id": "brand",
                        "required": True,
                        "variable_codes": ["brand_name"],
                    },
                ]
            },
            "evidence_bundle": {
                "items": [
                    {
                        "id": "ev-case",
                        "value": "89㎡项目增加12㎡收纳",
                        "verified_status": "retrieved",
                        "allowed_usage": ["title"],
                    }
                ]
            },
            "product_evidence_pack": {
                "slot_mappings": [
                    {
                        "slot": "case_proof",
                        "target_usage": "title",
                        "evidence_ids": ["ev-case"],
                        "integration_instruction": "使用案例结果",
                    },
                    {
                        "slot": "brand",
                        "target_usage": "title",
                        "evidence_ids": ["ev-brand"],
                        "integration_instruction": "使用品牌名",
                    },
                ]
            },
            "title_candidates": [
                {"id": "t1", "text": "收纳改造效果看得见", "formula_code": "T02", "evidence_ids": []},
                {"id": "t2", "text": "改善家庭收纳新方案", "formula_code": "T02", "evidence_ids": []},
            ],
        },
        node_run_id="node-title",
    )

    report = result["title_validation_report"]
    assert report["status"] == "blocked"
    assert [item["text"] for item in report["items"]] == ["收纳改造效果看得见", "改善家庭收纳新方案"]
    assert report["items"][0]["missing_required_slots"] == ["case_proof"]
    assert result["title_evidence_requirements"] == [
        {
            "slot": "case_proof",
            "required": True,
            "evidence_ids": ["ev-case"],
            "integration_instruction": "使用案例结果",
        },
        {
            "slot": "brand",
            "required": False,
            "evidence_ids": ["ev-brand"],
            "integration_instruction": "使用品牌名",
        },
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_title_slot_requirements_do_not_duplicate_a_result_covered_by_case_evidence():
    result = await V3DeterministicNodeHandler().execute(
        db=object(),
        node={"id": "validate_title_candidates"},
        state={
            "formula_selection_snapshot": {"selected_title_formula_code": "T02"},
            "strategy_snapshot": {
                "title_formula": {"variable_schema": ["emotion", "number", "result"]},
                "body_formula": {"required_variables": ["product", "result"]},
                "creation_method_definitions": [],
            },
            "product_material_requirements": {
                "requirements": [
                    {
                        "requirement_id": "product_profile",
                        "required": True,
                        "variable_codes": ["product", "result"],
                    },
                    {
                        "requirement_id": "case_proof",
                        "required": True,
                        "variable_codes": ["number", "result", "scene"],
                    },
                    {"requirement_id": "brand", "required": False, "variable_codes": []},
                ]
            },
            "evidence_bundle": {
                "items": [
                    {
                        "id": "ev-case",
                        "value": "89㎡项目增加12㎡收纳",
                        "verified_status": "retrieved",
                        "allowed_usage": ["title"],
                    }
                ]
            },
            "product_evidence_pack": {
                "slot_mappings": [
                    {
                        "slot": "product_profile",
                        "target_usage": "title",
                        "evidence_ids": ["ev-product"],
                        "integration_instruction": "可呈现服务主体",
                    },
                    {
                        "slot": "case_proof",
                        "target_usage": "title",
                        "evidence_ids": ["ev-case"],
                        "integration_instruction": "使用案例数字和结果",
                    },
                    {
                        "slot": "brand",
                        "target_usage": "title",
                        "evidence_ids": ["ev-brand"],
                        "integration_instruction": "可作为品牌背书",
                    },
                ]
            },
            "title_candidates": [
                {
                    "id": "t1",
                    "text": "没想到，杭州89㎡竟多出12㎡收纳",
                    "formula_code": "T02",
                    "evidence_ids": ["ev-case"],
                },
                {
                    "id": "t2",
                    "text": "惊喜！89㎡改造增加12㎡收纳",
                    "formula_code": "T02",
                    "evidence_ids": ["ev-case"],
                },
            ],
        },
        node_run_id="node-title",
    )

    assert result["title_validation_report"]["status"] == "passed"
    assert [(item["slot"], item["required"]) for item in result["title_evidence_requirements"]] == [
        ("product_profile", False),
        ("case_proof", True),
        ("brand", False),
    ]


@pytest.mark.unit
def test_strategy_product_research_uses_agent_configured_knowledge_scope():
    context = type("Context", (), {"knowledges": ["company-kb", "other-kb"], "max_execution_steps": 20})()
    request = AgentDelegationRequest(
        task_id="task-1",
        parent_content_run_id="run-1",
        node_run=type("NodeRun", (), {"node_id": "collect_strategy_product_evidence"})(),
        user=object(),
        agent_slug="content-research-agent",
        required_skills=("strategy-product-researcher",),
        input_contract="CollectStrategyProductEvidenceInputV1",
        input_payload={},
        input_snapshot_hash="hash",
        domain_context=ContractDomainContext(),
        governance_values={},
        prompt="research",
        output_contract="ProductEvidenceCollectionResultV1",
        knowledge_policy="agent_scope",
    )

    AgentDelegationService._apply_node_constraints(context, request)

    assert context.knowledges == ["company-kb", "other-kb"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_strategy_product_price_requires_explicit_human_confirmation():
    agent = ContentWorkflowAgent()
    graph = StateGraph(ContentWorkflowState)

    async def confirm(state: ContentWorkflowState):
        return await agent._v3_human_review(
            {"id": "confirm_strategy_product_facts", "interrupt_type": "strategy_product_facts"},
            state,
        )

    graph.add_node("confirm", confirm)
    graph.add_edge(START, "confirm")
    graph.add_edge("confirm", END)
    workflow = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "product-price-confirmation"}}
    item = {
        "id": "ev-price",
        "value": "套餐报价 12800 元",
        "risk_level": "high_risk",
        "verified_status": "retrieved",
        "metadata": {"material_type": "price", "effective_at": "2026-08-01"},
    }
    state = {
        "task_id": "task-1",
        "run_id": "run-1",
        "state_version": 3,
        "strategy_snapshot": _strategy_snapshot(),
        "product_evidence_collection": {"evidence_items": [item]},
    }

    await workflow.ainvoke(state, config=config)
    interrupted = await workflow.aget_state(config)
    payload = interrupted.interrupts[0].value
    assert payload["interrupt_type"] == "strategy_product_facts"
    assert payload["evidence_items"][0]["value"] == "套餐报价 12800 元"

    await workflow.ainvoke(
        Command(
            resume={
                "run_id": "run-1",
                "node_id": "confirm_strategy_product_facts",
                "expected_state_version": 3,
                "confirmed_evidence_ids": ["ev-price"],
            }
        ),
        config=config,
    )
    completed = await workflow.aget_state(config)
    confirmed = completed.values["product_evidence_collection"]["evidence_items"][0]
    assert confirmed["verified_status"] == "user_confirmed"
