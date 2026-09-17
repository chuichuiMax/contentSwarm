"""检查部署中的装修词库：真实数据库读取及加载事件，不调用模型。"""

import uuid

import pytest

from yuxi.content.control.workflow.deterministic_node import V3DeterministicNodeHandler
from yuxi.services.run_queue_service import get_redis_client, list_run_stream_events
from yuxi.storage.postgres.manager import pg_manager


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_deployed_frt09_frb08_lexicons_load_and_emit_event():
    pg_manager.initialize()
    run_id = f"lexicon_test_{uuid.uuid4().hex}"
    try:
        async with pg_manager.AsyncSession() as db:
            result = await V3DeterministicNodeHandler._load_formula_lexicons(
                db=db,
                node_run_id=run_id,
                state={
                    "run_id": run_id,
                    "task_id": run_id,
                    "industry_pack_version_id": "industry-pack-decoration-v4",
                    "strategy_snapshot": {"title_formula": {"code": "FRT09"}, "body_formula": {"code": "FRB08"}},
                },
            )
        bundle = result["formula_lexicon_bundle"]
        assert [item["code"] for item in bundle["title"]] == ["title.positioning", "title.house_type"]
        assert [item["code"] for item in bundle["body"]] == [
            "body.budget_pain",
            "body.professional_answer",
            "ending.quotation_cta",
        ]
        for item in [*bundle["title"], *bundle["body"]]:
            assert item["chunks"] and all(chunk.strip() for chunk in item["chunks"]), item["filename"]
            assert item["knowledge_base_id"] and item["file_id"]
        events = await list_run_stream_events(run_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "content.formula_lexicons.loaded"
        assert events[0]["payload"]["payload"]["bundle_hash"] == bundle["bundle_hash"]
    finally:
        redis = await get_redis_client()
        await redis.delete(f"run:events:{run_id}")
        await pg_manager.async_engine.dispose()
