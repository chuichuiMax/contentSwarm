"""标题候选可按本篇语义选择，仍校验事实与使用记录。"""

from types import SimpleNamespace

import pytest

from yuxi.content.model.contracts import ContentNodeResultCollector, ContractDomainContext, get_contract_model
from yuxi.content.model.materials import select_formula_lexicon_terms


@pytest.mark.asyncio
async def test_title_collector_records_chosen_candidate_without_forcing_first_term():
    terms = ["劝退", "听劝", "谁懂啊"]
    selection = select_formula_lexicon_terms({"title": {"title.oral_emotion": terms}, "body": {}})
    context = ContractDomainContext(
        locked_title_formula_code="FRT16",
        locked_body_formula_code="FRB16",
        required_title_lexicon_codes=frozenset({"title.oral_emotion"}),
        allowed_title_lexicon_terms={"title.oral_emotion": frozenset(terms)},
        locked_title_lexicon_terms={"title.oral_emotion": tuple(selection["title"]["title.oral_emotion"])},
    )
    payload = get_contract_model("GeneratedContentResultV1").model_validate(
        {
            "title": {"text": "工长聊拆除：听劝，先定范围", "formula_code": "FRT16", "evidence_ids": []},
            "outline": {
                "body_formula_code": "FRB16",
                "sections": [{"section_id": "s1", "goal": "范围", "evidence_ids": []}],
            },
            "draft": {"body": "拆除先确认范围。", "body_formula_code": "FRB16", "topics": [], "paragraph_evidence": []},
        }
    )
    collector = ContentNodeResultCollector(
        "GeneratedContentResultV1",
        context,
        SimpleNamespace(_required_skill_closure=[], _activated_required_skills=[]),
    )
    await collector.submit(title=payload.title, outline=payload.outline, draft=payload.draft)
    assert collector.finalize()["title"]["lexicon_usage"] == [
        {"code": "title.oral_emotion", "selected_terms": ["听劝"]}
    ]
