from __future__ import annotations

import pytest

from yuxi.content.model.contracts import (
    ContractDomainContext,
    ContractDomainValidationError,
    validate_content_node_result,
)
from yuxi.content.v3.body_calling import DECORATION_BODY_CALLING, get_decoration_body_calling
from yuxi.content.v3.formula_lexicons import TITLE_FORMULA_LEXICON_CODES, get_formula_lexicon_requirements


def _outline_payload(*, formula_code: str, section_ids: list[str], variant_key: str | None = None) -> dict:
    return {
        "body_formula_code": formula_code,
        "sections": [
            {"section_id": section_id, "goal": section_id, "evidence_ids": []}
            for section_id in section_ids
        ],
        "variant_key": variant_key,
    }


def _context(formula_code: str) -> ContractDomainContext:
    calling = get_decoration_body_calling(formula_code)
    return ContractDomainContext(
        locked_body_formula_code=formula_code,
        locked_body_calling_section_ids=tuple(section["id"] for section in calling["sections"]),
        allowed_body_variant_keys=frozenset(variant["id"] for variant in calling["variants"]),
        allowed_evidence_by_usage={"body": frozenset()},
    )


def test_body_calling_catalog_covers_all_formulas_and_five_source_columns() -> None:
    assert set(DECORATION_BODY_CALLING) == {"C01", "C02", "C03", "C04"}
    for calling in DECORATION_BODY_CALLING.values():
        assert calling["formula_name"]
        assert calling["lexicon_calls"]
        assert calling["sections"]
        assert all(section["fill_rule"] for section in calling["sections"])
        assert calling["reference_examples"]


def test_outline_must_follow_body_calling_section_order_and_choose_one_variant() -> None:
    calling = get_decoration_body_calling("C01")
    section_ids = [section["id"] for section in calling["sections"]]

    result = validate_content_node_result(
        "OutlineResultV1",
        _outline_payload(formula_code="C01", section_ids=section_ids, variant_key="service_contrast"),
        _context("C01"),
    )
    assert result.variant_key == "service_contrast"

    with pytest.raises(ContractDomainValidationError, match="必须按锁定调用规则输出段落"):
        validate_content_node_result(
            "OutlineResultV1",
            _outline_payload(
                formula_code="C01",
                section_ids=list(reversed(section_ids)),
                variant_key="service_contrast",
            ),
            _context("C01"),
        )

    with pytest.raises(ContractDomainValidationError, match="单一维度"):
        validate_content_node_result(
            "OutlineResultV1",
            _outline_payload(formula_code="C01", section_ids=section_ids),
            _context("C01"),
        )


def test_formula_without_variants_rejects_extra_variant() -> None:
    calling = get_decoration_body_calling("C02")
    section_ids = [section["id"] for section in calling["sections"]]
    with pytest.raises(ContractDomainValidationError, match="不允许选择额外反差维度"):
        validate_content_node_result(
            "OutlineResultV1",
            _outline_payload(formula_code="C02", section_ids=section_ids, variant_key="service_contrast"),
            _context("C02"),
        )


def test_formula_lexicon_requirements_cover_all_title_formulas_and_locked_body_formula() -> None:
    assert set(TITLE_FORMULA_LEXICON_CODES) == {f"T{index:02d}" for index in range(1, 8)}
    requirements = get_formula_lexicon_requirements("T01", "C02")
    assert [item["filename"] for item in requirements["title"]] == [
        "人群定位资料库-人群词库.txt",
        "结果价值资料库-正向结果词库.txt",
    ]
    assert [item["filename"] for item in requirements["body"]] == [
        "实景改造资料库-旧房痛点词库.txt",
        "实景改造资料库-改造优势词库.txt",
        "人设价值资料库-落地背书词库.txt",
        "结尾引导资料库-案例引导词库.txt",
    ]


def test_generated_content_must_report_formula_lexicon_usage() -> None:
    context = ContractDomainContext(
        locked_title_formula_code="T01",
        locked_body_formula_code="C02",
        required_title_lexicon_codes=frozenset({"title.audience", "title.positive_result"}),
        allowed_body_lexicon_codes=frozenset({"body.old_house_pain", "body.renovation_advantage"}),
        allowed_evidence_by_usage={"title": frozenset(), "body": frozenset()},
    )
    payload = {
        "title": {
            "text": "标题",
            "formula_code": "T01",
            "evidence_ids": [],
            "lexicon_usage": [
                {"code": "title.audience", "selected_terms": ["老房改造业主"]},
                {"code": "title.positive_result", "selected_terms": ["省心完工"]},
            ],
        },
        "outline": {
            "body_formula_code": "C02",
            "sections": [{"section_id": "opening", "goal": "开篇", "evidence_ids": []}],
        },
        "draft": {
            "body": "正文",
            "topics": [],
            "paragraph_evidence": [],
            "body_formula_code": "C02",
            "lexicon_usage": [
                {"code": "body.old_house_pain", "selected_terms": ["收纳不足"]},
                {"code": "body.renovation_advantage", "selected_terms": ["动线合理"]},
            ],
        },
    }
    validate_content_node_result("GeneratedContentResultV1", payload, context)

    payload["title"]["lexicon_usage"].pop()
    with pytest.raises(ContractDomainValidationError, match="标题必须使用"):
        validate_content_node_result("GeneratedContentResultV1", payload, context)
