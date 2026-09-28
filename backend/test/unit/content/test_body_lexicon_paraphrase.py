"""正文词条记录表达来源，允许按全文口吻改写。"""

from types import SimpleNamespace

import pytest

from yuxi.content.model.contracts import ContentNodeResultCollector, ContractDomainContext, get_contract_model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "term,body",
    [
        ("国标施工规范", "拿不准怎么做的地方，先把对应的施工要求弄清楚，再接着往下做。"),
        ("报价对比", "把要做的项目列在一起看看，哪里没说明白，咱们就再聊清楚。"),
    ],
)
async def test_collector_accepts_body_paraphrase_and_retains_source_term(term, body):
    code = "body.professional_answer"
    context = ContractDomainContext(
        locked_title_formula_code="FRT16",
        locked_body_formula_code="FRB16",
        allowed_body_lexicon_codes=frozenset({code}),
        allowed_body_lexicon_terms={code: frozenset({term})},
        locked_body_lexicon_terms={code: (term,)},
    )
    payload = get_contract_model("GeneratedContentResultV1").model_validate(
        {
            "title": {"text": "施工前先把要求聊清楚", "formula_code": "FRT16", "evidence_ids": []},
            "outline": {
                "body_formula_code": "FRB16",
                "sections": [{"section_id": "s1", "goal": "说明施工要求", "evidence_ids": []}],
            },
            "draft": {"body": body, "body_formula_code": "FRB16", "topics": [], "paragraph_evidence": []},
        }
    )
    collector = ContentNodeResultCollector(
        "GeneratedContentResultV1",
        context,
        SimpleNamespace(_required_skill_closure=[], _activated_required_skills=[]),
    )

    await collector.submit(title=payload.title, outline=payload.outline, draft=payload.draft)

    result = collector.finalize()
    assert term not in result["draft"]["body"]
    assert result["draft"]["body"] == body
    assert result["draft"]["lexicon_usage"] == [{"code": code, "selected_terms": [term]}]
