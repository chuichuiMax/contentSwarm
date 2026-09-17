from copy import deepcopy

from yuxi.content.v3.decoration_only import decoration_rule_bundle


def test_cleanup_removes_foreign_rules_and_preserves_shared_decoration_rules():
    bundle = {
        "methods": [{"code": "M1", "industry_scope": []}, {"code": "M2", "industry_scope": ["food"]}],
        "title_formulas": [
            {
                "code": "T1",
                "industry_scope": ["decoration", "food"],
                "compatible_methods": ["M1", "M2"],
                "source_content": {"cross_industry": {"name": "餐饮"}, "keep": 1},
            }
        ],
        "content_formulas": [
            {"code": "B1", "industry_scope": [], "industry_aliases": {"food": "餐饮", "decoration": "装修"}}
        ],
        "combination_rules": [
            {"code": "D", "industry_scope": ["decoration"]},
            {"code": "F", "industry_scope": ["food"]},
        ],
    }
    original = deepcopy(bundle)
    cleaned = decoration_rule_bundle(bundle)
    assert bundle == original
    assert [item["code"] for item in cleaned["methods"]] == ["M1"]
    assert [item["code"] for item in cleaned["combination_rules"]] == ["D"]
    assert cleaned["title_formulas"][0]["source_content"] == {"keep": 1}
    assert cleaned["title_formulas"][0]["compatible_methods"] == ["M1"]
    assert cleaned["content_formulas"][0]["industry_aliases"] == {"decoration": "装修"}
    assert all(item["industry_scope"] == ["decoration"] for section in cleaned.values() for item in section)
    assert decoration_rule_bundle(cleaned) == cleaned
