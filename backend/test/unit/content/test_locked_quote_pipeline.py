from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from yuxi.content.control.workflow.deterministic_node import (
    V3DeterministicNodeHandler,
    _required_title_fact_options,
    _trusted_quote_evidence_items,
)
from yuxi.content.control.workflow.generation_input import (
    _project_standardized_production_pack,
    _redact_locked_quote_from_review,
)
from yuxi.content.model.locked_blocks import extract_locked_quote_block, render_semicolon_lines


def _quote_material(original: str = "拆除：1000元；水电：2400元", *, render_policy: str = "checkmark-lines-v1") -> dict:
    content_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
    return {
        "id": "mat-quote",
        "material_type": "business_fact",
        "variable_codes": ["quote_block"],
        "evidence_ids": ["ev-quote"],
        "payload": {
            "value": {
                "original_content": original,
                "content_hash": content_hash,
                "render_policy": render_policy,
                "insertion_policy": "after-opening-paragraph-v1",
            }
        },
        "source": {
            "source_type": "business_record",
            "source_id": "dangjia:001:quote-block",
            "source_version": "001",
            "source_hash": content_hash,
        },
        "governance": {
            "review_status": "approved",
            "verified_status": "user_confirmed",
            "risk_level": "high_risk",
            "allowed_usage": ["body"],
        },
    }


def _pack(*, render_policy: str = "checkmark-lines-v1") -> dict:
    return {
        "schema_version": 1,
        "id": "pack-1",
        "task_id": "task-1",
        "production_order": {},
        "material_manifest": {},
        "material_quality_report": {},
        "materials": [
            _quote_material(render_policy=render_policy),
            {
                "id": "mat-label",
                "material_type": "business_fact",
                "variable_codes": ["title_price_label"],
                "evidence_ids": ["ev-label"],
                "payload": {"value": "整套人工合计"},
                "source": {
                    "source_type": "business_record",
                    "source_id": "dangjia:001:title-price-label",
                    "source_version": "001",
                    "source_hash": "a" * 64,
                },
                "governance": {
                    "review_status": "approved",
                    "verified_status": "user_confirmed",
                    "risk_level": "high_risk",
                    "allowed_usage": ["title", "body"],
                },
            },
            {
                "id": "mat-title-price",
                "material_type": "business_fact",
                "variable_codes": ["title_price"],
                "evidence_ids": ["ev-title-price"],
                "payload": {"value": "1.16w"},
                "source": {
                    "source_type": "business_record",
                    "source_id": "dangjia:001:title-price",
                    "source_version": "001",
                    "source_hash": "b" * 64,
                },
                "governance": {
                    "review_status": "approved",
                    "verified_status": "user_confirmed",
                    "risk_level": "high_risk",
                    "allowed_usage": ["title"],
                },
            },
        ],
        "strategy_snapshot": {},
        "formula_lexicon_bundle": {},
        "reference_snapshot": {},
        "expression_guidance": None,
        "writing_request": None,
        "channel_profile": {"body_constraints": {"max_length": 1000}},
        "persona_profile": {},
        "content_rule_bundle": {},
        "evidence_bundle_id": "bundle-1",
        "evidence_bundle_version": 1,
        "evidence_bundle_hash": "c" * 64,
        "formula_lexicon_bundle_hash": "d" * 64,
        "production_pack_hash": "e" * 64,
        "frozen_at": "2026-09-21T00:00:00Z",
        "compliance_policy_version_ids": [],
    }


@pytest.mark.parametrize("render_policy", ["semicolon-lines-v1", "checkmark-lines-v1"])
def test_trusted_quote_snapshot_becomes_four_confirmed_high_risk_facts(render_policy):
    original = "  拆除：1000元；水电：2400元  "
    snapshot = {
        "schema_version": 1,
        "source": "dangjia",
        "serial_no": "001",
        "content_type_code": "CT03",
        "quote_format": "单价面积",
        "quote_type": "standard_unit_price",
        "title_price": {"label": "整套人工合计", "display_text": "1.16w"},
        "quote_block": {
            "original_content": original,
            "content_hash": hashlib.sha256(original.encode("utf-8")).hexdigest(),
            "render_policy": render_policy,
            "insertion_policy": "after-opening-paragraph-v1",
        },
    }
    items = _trusted_quote_evidence_items(snapshot, content_type_code="CT03")

    assert {item.variable_codes[0] for item in items} == {
        "title_price",
        "title_price_label",
        "quote_type",
        "quote_block",
    }
    assert all(item.source_type == "business_record" for item in items)
    assert all(item.verified_status == "user_confirmed" and item.risk_level == "high_risk" for item in items)
    quote = next(item for item in items if item.variable_codes == ("quote_block",))
    assert quote.value["original_content"] == original
    assert quote.value["render_policy"] == render_policy
    legacy_id = "ev_" + hashlib.sha256(f"dangjia:001:quote-block:{quote.source_hash}".encode()).hexdigest()[:24]
    if render_policy == "semicolon-lines-v1":
        assert quote.id == legacy_id
    else:
        assert quote.id != legacy_id
        repeated_quote = next(
            item
            for item in _trusted_quote_evidence_items(snapshot, content_type_code="CT03")
            if item.variable_codes == ("quote_block",)
        )
        assert repeated_quote.id == quote.id


def test_generation_pack_projection_never_contains_quote_original_or_source_hash():
    original = _quote_material()["payload"]["value"]["original_content"]
    projected = _project_standardized_production_pack(_pack())
    serialized = json.dumps(projected, ensure_ascii=False, sort_keys=True)

    assert original not in serialized
    assert "content_hash" not in serialized
    assert "source_hash" not in serialized
    assert projected["locked_blocks"][0]["block_id"] == "quote_block"
    quote = next(item for item in projected["materials"] if "quote_block" in item["variable_codes"])
    assert quote["payload"]["value"]["locked"] is True


def test_generation_pack_projection_only_exposes_quality_gate_bound_facts():
    pack = deepcopy(_pack())
    pack["materials"].append(
        {
            "id": "mat-unbound-advantage",
            "material_type": "business_fact",
            "variable_codes": ["advantages"],
            "payload": {"value": ["知识库通用优势"]},
            "source": {
                "source_type": "knowledge_base",
                "source_id": "kb/chunk-1",
                "source_version": "v1",
                "source_hash": "u" * 64,
            },
            "governance": {
                "review_status": "approved",
                "verified_status": "confirmed",
                "risk_level": "normal",
                "allowed_usage": ["body"],
            },
        }
    )
    pack["material_quality_report"] = {
        "bindings": [
            {"requirement_id": "variable:quote_block", "material_ids": ["mat-quote"]},
            {"requirement_id": "variable:title_price_label", "material_ids": ["mat-label"]},
            {"requirement_id": "variable:title_price", "material_ids": ["mat-title-price"]},
        ]
    }

    projected = _project_standardized_production_pack(pack)

    assert "mat-unbound-advantage" not in {item["id"] for item in projected["materials"]}


def test_generation_repair_report_never_leaks_locked_quote_original():
    original = _quote_material()["payload"]["value"]["original_content"]
    report = {
        "status": "blocked",
        "checks": [
            {
                "code": "COMPOSITION_ALIGNMENT",
                "message": f"不要重复输出：{original}",
                "location": original,
            }
        ],
    }

    projected = _redact_locked_quote_from_review(report, _pack())
    serialized = json.dumps(projected, ensure_ascii=False)

    assert original not in serialized
    assert "[程序锁定报价块]" in serialized


def test_generation_pack_projection_enriches_old_frt_title_formula_for_the_model():
    pack = _pack()
    pack["strategy_snapshot"] = {
        "industry_slug": "decoration",
        "title_formula": {
            "code": "FRT01",
            "variable_schema": ["location", "quantity", "product", "title_price"],
        },
    }

    projected = _project_standardized_production_pack(pack)
    formula = projected["strategy_snapshot"]["title_formula"]

    assert [slot["label"] for slot in formula["source_content"]["slot_schema"]] == [
        "地域",
        "面积/房型",
        "业务",
        "价格",
    ]
    assert formula["reference_examples"] == ["天津135平叠拼半包8.9w"]


def test_title_validation_requires_exact_confirmed_title_price():
    required = _required_title_fact_options(
        {},
        {"title_formula": {"variable_schema": ["title_price"]}},
        _pack(),
    )
    assert required == {"title_price": ("1.16w",)}


def test_semicolon_rendering_only_adds_missing_visual_newlines():
    assert render_semicolon_lines("拆除：1000元；水电：2400元；\n泥工：5000元") == (
        "拆除：1000元；\n水电：2400元；\n泥工：5000元"
    )


def test_checkmark_quote_preserves_prices_units_whitespace_and_source_hash():
    original = "24 墙拆除：40 元 /㎡；门洞加宽（30cm 内）：50元 / 个；\r\n\r\n拆卫生间（4㎡内）：1000/项；\n"
    material = _quote_material(original)
    before = deepcopy(material)

    quote = extract_locked_quote_block({"materials": [material]})

    assert quote["rendered_content"] == (
        "✅ 24 墙拆除：40 元 /㎡；\n✅ 门洞加宽（30cm 内）：50元 / 个；\r\n\r\n✅ 拆卫生间（4㎡内）：1000/项；\n"
    )
    assert quote["original_content"] == original
    assert quote["content_hash"] == hashlib.sha256(original.encode()).hexdigest()
    assert material == before


def test_checkmark_quote_does_not_duplicate_existing_checkmarks():
    quote = extract_locked_quote_block({"materials": [_quote_material("✅ 拆除：1000元；\n✅水电：2400元")]})
    assert quote["rendered_content"] == "✅ 拆除：1000元；\n✅水电：2400元"


@pytest.mark.parametrize("tamper", ["content", "render_policy"])
def test_checkmark_quote_rejects_tampered_prices_and_unpublished_policy(tamper):
    material = _quote_material()
    if tamper == "content":
        material["payload"]["value"]["original_content"] = "拆除：1元；水电：2400元"
    else:
        material["payload"]["value"]["render_policy"] = "unknown-v1"
    with pytest.raises(ValueError, match="Hash|未发布"):
        extract_locked_quote_block({"materials": [material]})


@pytest.mark.parametrize("render_policy", ["semicolon-lines-v1", "checkmark-lines-v1"])
@pytest.mark.asyncio
async def test_locked_quote_is_composed_once_and_then_validated(render_policy):
    creative_body = "开场说明。" * 25 + "\n\n" + "报价阅读提示和避坑建议。" * 12
    state = {
        "production_pack": _pack(render_policy=render_policy),
        "content_draft": {
            "body": creative_body,
            "topics": ["长沙装修"],
            "paragraph_evidence": [],
            "body_formula_code": "FRB07",
        },
        "selected_title": {"text": "长沙三室两厅装修1.16w"},
        "channel_profile": {"body_constraints": {"max_length": 1000}},
        "compliance_policies": [],
    }

    composed = await V3DeterministicNodeHandler._compose_locked_quote_block(
        db=None,
        state=state,
        node_run_id="node-1",
    )
    rendered = "拆除：1000元；\n水电：2400元"
    if render_policy == "checkmark-lines-v1":
        rendered = "✅ 拆除：1000元；\n✅ 水电：2400元"
    assert composed["content_draft"]["body"].count(rendered) == 1
    assert composed["content_draft"]["body"].index(rendered) < composed["content_draft"]["body"].index("报价阅读提示")
    assert composed["creative_content_draft"]["body"] == creative_body
    assert composed["creative_draft_hash"] != composed["final_draft_hash"]

    validation = await V3DeterministicNodeHandler._validate_composed_content(
        db=None,
        state={**state, **composed},
        node_run_id="node-2",
    )
    assert validation["composed_content_validation_report"]["status"] == "passed"

    recomposed = await V3DeterministicNodeHandler._compose_locked_quote_block(
        db=None,
        state={**state, **composed},
        node_run_id="node-3",
    )
    assert recomposed["content_draft"]["body"].count(rendered) == 1
    assert recomposed["final_draft_hash"] == composed["final_draft_hash"]


@pytest.mark.parametrize("has_quote", [True, False])
def test_generation_opening_accounts_for_inserted_quote_without_changing_frozen_pack(has_quote):
    pack = _pack()
    if not has_quote:
        pack["materials"] = []
    pack["content_rule_bundle"] = {
        "runtime_rules": {
            "viral-persona-author": {
                "opening_required": True,
                "opening_window_paragraphs": 2,
            }
        }
    }
    before = deepcopy(pack)
    projected = _project_standardized_production_pack(pack)
    assert pack == before
    instruction = projected["creative_opening_instruction"]
    assert ("创作稿第一段" if has_quote else "前 2 个自然段") in instruction
    assert "original_content" not in json.dumps(projected, ensure_ascii=False)
