"""使用现有受管 Agent 运行时异步准备爆款资产，不依赖创作任务。"""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select

from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import prepare_agent_runtime_context
from yuxi.content.model.contracts import ContentNodeResultCollector, ContractDomainContext
from yuxi.content.model.viral_assets import ViralArticleSource, validate_prepared_asset
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.agent_runtime_service import resolve_agent_runtime_context
from yuxi.services.content_viral_assets import accessible_asset_kbs, check_asset_source, preparation_skill_hash
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentViralArticleVersion


async def process_viral_asset(_ctx, asset_id: str, attempt: int):
    run_id = None
    try:
        async with pg_manager.get_async_session_context() as db:
            asset = (
                await db.execute(
                    select(ContentViralArticleVersion)
                    .where(
                        ContentViralArticleVersion.id == asset_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if asset is None or asset.status != "pending" or asset.attempt != attempt:
                return
            asset.status = "running"
            await db.commit()
            user = (
                await db.execute(select(User).where(User.uid == asset.created_by, User.is_deleted == 0))
            ).scalar_one_or_none()
            if user is None or asset.kb_id not in await accessible_asset_kbs(user):
                raise ValueError("原文访问权限已失效")
            if not await check_asset_source(db, asset):
                raise ValueError("原文已更新，请重新导入")
            if asset.preparation_skill_hash != preparation_skill_hash():
                raise ValueError("准备 Skill 已更新，请重新导入")
            source = ViralArticleSource.model_validate(asset.source_json)
            context = await resolve_agent_runtime_context(db=db, user=user, bound_agent_id="content-viral-asset-agent")
            agent = await AgentRepository(db).get_visible_by_slug(slug="content-viral-asset-agent", user=user)
            backend = agent_manager.get_agent(agent.backend_id)
            run_id = f"run_{uuid.uuid4().hex}"
            context.thread_id, context.run_id = f"viral:{uuid.uuid4().hex}", run_id
            context.request_id = f"viral:{uuid.uuid4().hex}"
            context.required_skills = ["viral-asset-preparer"]
            context.knowledges = []
            await prepare_agent_runtime_context(context, context_schema=backend.context_schema)
            collector = ContentNodeResultCollector(
                "ViralAssetPreparationResultV1",
                ContractDomainContext(viral_source=source.model_dump()),
                context,
            )
            context._content_node_result_collector = collector
            context._content_node_output_contract = "ViralAssetPreparationResultV1"
            context._content_node_result_tool_name = "submit_content_node_result"
            context._content_node_max_tool_calls = 1
            context._content_node_token_budget = 12000
            context._content_node_tool_scope = ["submit_content_node_result"]
            payload = {"source": source.model_dump(), "source_hash": source.source_hash}
            runtime_snapshot = {
                "model": str(getattr(context, "model", "")),
                "skills": getattr(context, "_runtime_skill_snapshots", []) or [],
                "preparation_skill_hash": asset.preparation_skill_hash,
            }
            runs = AgentRunRepository(db)
            await runs.create_run(
                run_id=run_id,
                thread_id=context.thread_id,
                agent_id=agent.slug,
                uid=str(user.uid),
                request_id=context.request_id,
                run_type="viral_asset_preparation",
                input_payload={
                    "asset_id": asset.id,
                    "attempt": attempt,
                    "input": payload,
                    "runtime_config_snapshot": runtime_snapshot,
                },
            )
            await runs.mark_running(run_id)
            asset.agent_run_id = run_id
            await db.commit()
        graph = await backend.get_graph(context=context)
        async with asyncio.timeout(140):
            await graph.ainvoke(
                {"messages": [json.dumps(payload, ensure_ascii=False)]},
                context=context,
                config={"configurable": {"thread_id": context.thread_id, "uid": context.uid}, "recursion_limit": 12},
            )
        result = validate_prepared_asset(collector.finalize(), source)
        async with pg_manager.get_async_session_context() as db:
            asset = (
                await db.execute(
                    select(ContentViralArticleVersion)
                    .where(
                        ContentViralArticleVersion.id == asset_id,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if asset.status == "running" and asset.attempt == attempt:
                if not await check_asset_source(db, asset):
                    asset.status, asset.error_message = "invalidated", "原文在准备过程中更新"
                else:
                    asset.prepared_json = {
                        **result.model_dump(mode="json"),
                        "runtime_config_snapshot": runtime_snapshot,
                    }
                    asset.status = "ready" if result.status == "prepared" else "needs_review"
                    asset.error_message = "；".join(result.issues) or None
            await AgentRunRepository(db).set_terminal_status(run_id, status="completed")
            await db.commit()
    except (Exception, asyncio.CancelledError) as exc:
        async with pg_manager.get_async_session_context() as db:
            asset = (
                await db.execute(
                    select(ContentViralArticleVersion)
                    .where(
                        ContentViralArticleVersion.id == asset_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if asset and asset.status == "running" and asset.attempt == attempt:
                asset.status, asset.error_message = "failed", str(exc) or "资产准备被取消"
            if run_id:
                await AgentRunRepository(db).set_terminal_status(
                    run_id,
                    status="failed",
                    error_type="viral_asset_preparation_failed",
                    error_message=str(exc),
                )
            await db.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
