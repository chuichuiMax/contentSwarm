"""用私有任务快照回放原文仿写，不创建任务、不生成图片或发布内容。"""

import json
import os
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from yuxi.agents.models import load_chat_model
from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.content.model.raw_reference import assemble_article, project_input, topic_validation_checks
from yuxi.content.v3.modular_rules import build_modular_rule_bundle


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_raw_reference_keeps_business_numbers_in_real_model_replay():
    directory = os.getenv("RAW_REFERENCE_REPLAY_DIR")
    if not directory:
        pytest.skip("需指定私有任务快照目录")
    directory = Path(directory)
    state = json.loads((directory / "checkpoint.json").read_text())
    task = json.loads((directory / "task-snapshot.json").read_text())
    generation = next(node for node in task["nodes"] if node["node_id"] == "generate_content")
    original_input = generation["input_snapshot"]["model_visible_payload"]
    runtime = generation["input_snapshot"]["runtime_config_snapshot"]
    pack = state["production_pack"]
    pack["content_rule_bundle"] = build_modular_rule_bundle(state["content_brief"], single_blueprint=True)
    if candidate_path := os.getenv("RAW_REFERENCE_AUTHOR_PATH"):
        author = next(m for m in pack["content_rule_bundle"]["modules"] if m["slug"] == "single-blueprint-author")
        author["instructions"] = Path(candidate_path).read_text().split("---", 2)[2].strip()
    view = project_input({"production_pack": pack, "raw_business_json": original_input["原始业务JSON"]})
    assert view["原始业务JSON"] == original_input["原始业务JSON"]
    assert view["爆款原文"] == original_input["爆款原文"]
    model = load_chat_model(
        runtime["model"], reasoning_effort=runtime["reasoning_effort"], timeout=120, max_retries=0, streaming=True
    )

    response = await model.ainvoke([HumanMessage(content=json.dumps(view, ensure_ascii=False))])
    article = assemble_article(response.text, pack)
    state.update(selected_title=article["title"], content_draft=article["draft"])
    state.update(await V3DeterministicNodeHandler._adapt_to_channel(db=None, state=state, node_run_id="replay"))
    result = await V3DeterministicNodeHandler._deterministic_validate(db=None, state=state, node_run_id="replay")

    (directory / "numeric-replay-result.json").write_text(
        json.dumps({"article": article, "validation": result["validation_report"]}, ensure_ascii=False, indent=2)
    )
    assert result["validation_report"]["status"] in {"passed", "warning"}, result["validation_report"]
    assert topic_validation_checks(state["content_draft"]["topics"]) == []
