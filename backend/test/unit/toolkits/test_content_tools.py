from types import SimpleNamespace

import pytest
from langgraph.prebuilt.tool_node import _get_all_injected_args

from yuxi.agents.middlewares.content_node_result import ContentNodeResultMiddleware
from yuxi.agents.toolkits.content.tools import (
    _filter_strategy_rule_bundle,
    _runtime_uid,
    get_business_facts,
    get_creation_rule_bundle,
)


def test_strategy_rule_bundle_keeps_only_current_industry_and_content_type_candidates():
    bundle = {
        "methods": [{"code": "M01"}, {"code": "M02"}, {"code": "M03"}],
        "title_formulas": [{"code": "T01"}, {"code": "T02"}],
        "content_formulas": [{"code": "C01"}, {"code": "C02"}],
        "combination_rules": [
            {
                "id": "decoration-ct01",
                "industry_scope": ["decoration"],
                "content_type_codes": ["CT01"],
                "method_members": [{"method_code": "M01"}, {"method_code": "M03"}],
                "title_formula_candidate_codes": ["T01"],
                "body_formula_candidate_codes": ["C02"],
            },
            {
                "id": "decoration-ct02",
                "industry_scope": ["decoration"],
                "content_type_codes": ["CT02"],
                "method_members": [{"method_code": "M02"}],
                "title_formula_candidate_codes": ["T02"],
                "body_formula_candidate_codes": ["C01"],
            },
            {
                "id": "beauty-ct01",
                "industry_scope": ["beauty"],
                "content_type_codes": ["CT01"],
                "method_members": [{"method_code": "M02"}],
                "title_formula_candidate_codes": ["T02"],
                "body_formula_candidate_codes": ["C01"],
            },
        ],
    }

    filtered = _filter_strategy_rule_bundle(bundle, industry_slug="decoration", content_type_code="CT01")

    assert [item["id"] for item in filtered["combination_rules"]] == ["decoration-ct01"]
    assert [item["code"] for item in filtered["methods"]] == ["M01", "M03"]
    assert [item["code"] for item in filtered["title_formulas"]] == ["T01"]
    assert [item["code"] for item in filtered["content_formulas"]] == ["C02"]


@pytest.mark.parametrize(
    "runtime",
    [
        SimpleNamespace(context=SimpleNamespace(uid="user-from-context"), config={}),
        SimpleNamespace(context={"uid": "user-from-mapping"}, config={}),
        SimpleNamespace(context=None, config={"configurable": {"uid": "user-from-run-config"}}),
    ],
)
def test_runtime_uid_supports_langgraph_context_carriers(runtime):
    assert _runtime_uid(runtime).startswith("user-from-")


def test_runtime_uid_rejects_missing_identity():
    with pytest.raises(ValueError, match="无法获取当前用户"):
        _runtime_uid(SimpleNamespace(context=None, config={}))


@pytest.mark.parametrize("content_tool", [get_creation_rule_bundle, get_business_facts])
def test_content_tools_require_langgraph_runtime_injection(content_tool):
    assert _get_all_injected_args(content_tool).runtime == "runtime"


@pytest.mark.asyncio
async def test_content_node_middleware_propagates_runtime_to_content_tools():
    runtime = SimpleNamespace(context=SimpleNamespace(uid="user-from-middleware"), config={})
    request = SimpleNamespace(runtime=runtime)

    async def handler(_request):
        return _runtime_uid(None)

    result = await ContentNodeResultMiddleware().awrap_tool_call(request, handler)

    assert result == "user-from-middleware"
