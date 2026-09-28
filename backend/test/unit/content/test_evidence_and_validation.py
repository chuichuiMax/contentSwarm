import json

from yuxi.content.validators import (
    evidence_number_tokens,
    merge_evidence,
    normalize_manual_evidence,
    unsupported_number_tokens,
    validate_content,
)


def test_normalize_evidence_is_stable_and_source_backed():
    brief = {
        "brand": {"name": "山野咖啡"},
        "audience": ["附近上班族"],
        "business_variables": {"product": "手冲咖啡", "result": "每天限量30杯"},
        "form_values": {},
    }

    first = normalize_manual_evidence("ct_1", brief)
    second = normalize_manual_evidence("ct_1", brief)

    assert first == second
    assert first["items"]
    assert all(item["source_type"] == "manual_input" for item in first["items"])
    assert all(item["allowed_usage"] == ["title", "body"] for item in first["items"])


def test_merge_evidence_deduplicates_and_preserves_source_counts():
    base = {"items": [{"id": "ev_1", "source_type": "manual_input"}], "summary": {}}
    result = merge_evidence(
        base,
        [
            {"id": "ev_1", "source_type": "manual_input"},
            {"id": "ev_2", "source_type": "knowledge_base"},
        ],
    )

    assert [item["id"] for item in result["items"]] == ["ev_1", "ev_2"]
    assert result["summary"] == {"manual": 1, "knowledge": 1, "business_api": 0}


def test_deterministic_review_blocks_unsupported_numbers_and_claims():
    report = validate_content(
        title="7天保证见效",
        body="第一套方案",
        topics=[],
        brief={"required_terms": [], "forbidden_terms": []},
        evidence_bundle={"items": []},
        strategy={"methods": ["M01"], "title_formula_code": "T01", "content_formula_code": "C01"},
    )

    codes = {item["code"] for item in report["checks"]}
    assert report["status"] == "blocked"
    assert "FACT_NUMBER_WITHOUT_SOURCE" in codes
    assert "CONTENT_HIGH_RISK_CLAIM" in codes


def test_deterministic_review_accepts_numbers_in_shared_evidence_bundle():
    report = validate_content(
        title="12周学习计划",
        body="每周2次练习",
        topics=["英语启蒙"],
        brief={"required_terms": ["英语启蒙"], "forbidden_terms": []},
        evidence_bundle={"items": [{"id": "ev_1", "value": "12周，每周2次"}]},
        strategy={"methods": ["M01"], "title_formula_code": "T01", "content_formula_code": "C01"},
    )

    assert report == {"status": "passed", "checks": []}


def test_deterministic_review_accepts_work_years_from_structured_request():
    report = validate_content(
        title="长沙装修工长",
        body="在长沙做了5年装修工长，主要做水电和泥瓦。",
        topics=[],
        brief={"required_terms": [], "forbidden_terms": []},
        evidence_bundle={
            "items": [
                {
                    "id": "ev_request",
                    "value": json.dumps(
                        {"persona": {"workYears": "5", "skills": ["水电", "泥瓦"]}},
                        ensure_ascii=False,
                    ),
                }
            ]
        },
        strategy={"methods": ["M01"], "title_formula_code": "T01", "content_formula_code": "C01"},
    )

    assert report == {"status": "passed", "checks": []}


def test_deterministic_review_does_not_use_asset_id_as_work_years_evidence():
    report = validate_content(
        title="长沙装修工长",
        body="在长沙做了5年装修工长。",
        topics=[],
        brief={"required_terms": [], "forbidden_terms": []},
        evidence_bundle={
            "items": [
                {
                    "id": "ev_request",
                    "value": json.dumps({"images": [{"objectKey": "5.jpg"}]}),
                }
            ]
        },
        strategy={"methods": ["M01"], "title_formula_code": "T01", "content_formula_code": "C01"},
    )

    assert report["status"] == "blocked"
    assert any(item["message"] == "数字“5年”没有出现在证据包中" for item in report["checks"])


def test_price_unit_spacing_in_evidence_does_not_block_same_amount():
    evidence = {
        "items": [
            {
                "id": "ev-price",
                "value": "铲墙：20 元 /㎡ ×28㎡ =560 元；石膏板吊顶：70 元 /㎡ ×4㎡ =280 元",
            }
        ]
    }

    assert unsupported_number_tokens("铲墙20元/㎡×28㎡=560元，吊顶70元/㎡×4㎡=280元", evidence) == []
    assert unsupported_number_tokens("铲墙20元/㎡×28㎡=560元，额外收费999元", evidence) == ["999元"]


def test_unsupported_number_tokens_ignore_keycap_emoji_only():
    assert unsupported_number_tokens("1️⃣ 看范围，2⃣ 核材料，另收99元", {"items": []}) == ["99元"]


def test_square_meter_aliases_are_equivalent_but_changed_area_is_blocked():
    evidence = {"items": [{"id": "ev-area", "value": "115平"}]}

    assert unsupported_number_tokens("这套115㎡三室二厅先核对范围", evidence) == []
    assert unsupported_number_tokens("这套115平方米三室二厅先核对范围", evidence) == []
    assert unsupported_number_tokens("这套115.0平米三室二厅先核对范围", evidence) == []
    assert unsupported_number_tokens("这套116㎡三室二厅先核对范围", evidence) == ["116㎡"]


def test_derived_numeric_evidence_keeps_its_declared_unit():
    evidence = {
        "items": [
            {
                "value": 10680,
                "source_type": "human_confirmation",
                "metadata": {"unit": "元"},
            }
        ]
    }

    assert unsupported_number_tokens("程序计算参考为10680元", evidence) == []


def test_structured_trade_amounts_supply_their_declared_units():
    evidence = {
        "items": [
            {
                "value": [
                    {"trade": "砌筑找平", "amount": 6800, "unit": "元"},
                    {"trade": "防水施工", "amount": 5200, "unit": "元"},
                ],
                "source_type": "manual_input",
            }
        ]
    }

    assert unsupported_number_tokens("砌筑找平6800元，防水施工5200元", evidence) == []


def test_nested_labor_aux_breakdown_inherits_declared_unit():
    evidence = {
        "items": [
            {
                "id": "ev-labor-aux",
                "source_type": "manual_input",
                "value": {
                    "labor_total": 12800,
                    "auxiliary_total": 9000,
                    "unit": "元",
                    "trades": [{"trade": "基层处理", "labor_amount": 3600, "auxiliary_amount": 2200}],
                },
            }
        ]
    }

    assert unsupported_number_tokens("人工合计12800元，辅材合计9000元，基层3600元+2200元", evidence) == []


def test_persona_numeric_fields_supply_only_their_declared_units():
    evidence = {
        "items": [
            {
                "value": '"persona": {"workYears": "5", "servedSiteCount": "10", "ownerRecommendCount": "10"}',
                "source_type": "manual_input",
                "allowed_usage": ["body"],
            }
        ]
    }

    assert {"5年", "10个", "10位"} <= set(evidence_number_tokens(evidence))
    assert unsupported_number_tokens("做工长5年，服务10个工地，获10位业主推荐", evidence) == []
    assert unsupported_number_tokens("5万元报价，10天完工", evidence) == ["10天", "5万元"]
    evidence["items"][0]["source_type"] = "knowledge_base"
    assert unsupported_number_tokens("做工长5年", evidence) == ["5年"]


def test_quote_currency_shorthand_allows_yuan_without_allowing_new_amounts_or_units():
    evidence = {
        "items": [
            {
                "variable_codes": ["quote_block"],
                "source_type": "business_record",
                "value": {"original_content": "拆卫生间（4㎡内）：1000/项；拆地砖：18/㎡；拆厨房（5㎡内）：1400/项"},
            }
        ]
    }
    assert unsupported_number_tokens("拆卫生间1000元/项，拆地砖18元/㎡，拆厨房1400元/项", evidence) == []
    assert unsupported_number_tokens("拆卫生间1100元/项，18天", evidence) == ["1100元", "18天"]
    assert "4元" not in evidence_number_tokens(evidence)
