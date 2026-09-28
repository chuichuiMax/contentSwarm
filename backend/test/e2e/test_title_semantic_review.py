"""使用真实任务审核输入检验标题含义；独立于生成通过测试。"""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from yuxi.agents.models import load_chat_model
from yuxi.agents.skills.buildin import BUILTIN_SKILLS
from yuxi.content.model.contracts.content_nodes import StandardizedContentReviewResultV1


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_live_title_review_rejects_empty_emotion_but_accepts_complete_meaning():
    model_spec = os.getenv("TITLE_REVIEW_MODEL")
    replay_path = os.getenv("TITLE_REVIEW_REPLAY")
    output_path = os.getenv("TITLE_REVIEW_OUTPUT")
    if not model_spec or not replay_path or not output_path:
        pytest.skip("需配置真实模型、任务审核输入及输出路径")

    from yuxi.models.providers.cache import model_cache
    from yuxi.models.providers.service import get_all_model_providers
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        model_cache.rebuild(await get_all_model_providers(db))
    await pg_manager.close()
    spec = next(s for s in BUILTIN_SKILLS if s.slug == "viral-modular-reviewer")
    instructions = (Path(spec.source_dir) / "SKILL.md").read_text()
    model = load_chat_model(model_spec, reasoning_effort="medium", timeout=120, max_retries=0)
    model = model.bind_tools([StandardizedContentReviewResultV1], tool_choice="StandardizedContentReviewResultV1")
    original = json.loads(Path(replay_path).read_text())
    results = []
    for title, term, expected in [
        ("水电拆除，劝退", "劝退", "blocked"),
        ("工长拆除，真香", "真香", "blocked"),
        ("工长聊拆除，听劝", "听劝", "blocked"),
        ("工长聊拆除：听劝，先定范围", "听劝", "passed"),
    ]:
        payload = deepcopy(original)
        payload["selected_title"]["text"] = title
        payload["channel_result"]["title"] = title
        payload["selected_title"]["lexicon_usage"] = [{"code": "title.oral_emotion", "selected_terms": [term]}]
        # 同一历史输入保留完整正文、事实与槽位，只比较标题含义。
        response = await model.ainvoke(
            [
                SystemMessage(content=instructions),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        assert len(response.tool_calls) == 1
        report = StandardizedContentReviewResultV1.model_validate(response.tool_calls[0]["args"])
        check = next(c for c in report.checks if c.code == "TITLE_ALIGNMENT")
        results.append({"title": title, "expected": expected, "check": check.model_dump(mode="json")})
    Path(output_path).write_text(json.dumps(results, ensure_ascii=False, indent=2))
    assert all(r["check"]["status"] == r["expected"] for r in results), results
