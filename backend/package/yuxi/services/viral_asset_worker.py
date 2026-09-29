"""使用现有受管 Agent 运行时异步准备爆款资产，不依赖创作任务。"""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select

from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import prepare_agent_runtime_context
from yuxi.content.model.contracts import ContentNodeResultCollector, ContractDomainContext
from yuxi.content.model.viral_assets import ViralArticleSource, validate_prepared_asset_v2
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.agent_runtime_service import resolve_agent_runtime_context
from yuxi.services.content_viral_assets import (
    accessible_asset_kbs,
    check_asset_source,
    enqueue_asset,
    preparation_skill_hash,
    published_variable_codes,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentViralArticleVersion
from yuxi.storage.postgres.models_knowledge import KnowledgeBase

CREATION_TYPE_NAMES = {
    "CT01": "装修案例分享",
    "CT02": "装修报价清单",
    "CT03": "装修避坑分享",
    "CT04": "装修省钱攻略",
    "CT05": "施工报价",
    "CT06": "工艺施工展示",
    "CT07": "人设自荐",
}
MAX_INTERRUPT_RETRIES = 3


def _interrupt_count(asset: ContentViralArticleVersion) -> int:
    prepared = asset.prepared_json if isinstance(asset.prepared_json, dict) else {}
    try:
        return max(0, int(prepared.get("interrupt_count") or 0))
    except (TypeError, ValueError):
        return 0


def _failure_message(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message
    if isinstance(exc, TimeoutError):
        return "资产准备超时（900s）"
    if isinstance(exc, asyncio.CancelledError):
        return "资产准备被中断（任务取消或进程重启）"
    return type(exc).__name__


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
            allowed_variable_codes = sorted(await published_variable_codes(db))
            if not allowed_variable_codes:
                raise ValueError("已发布变量目录为空，无法准备参考槽位")
            kb_content_type_code = await db.scalar(
                select(KnowledgeBase.additional_params["viral_content_type"].as_string()).where(
                    KnowledgeBase.kb_id == asset.kb_id
                )
            )
            if kb_content_type_code not in CREATION_TYPE_NAMES:
                raise ValueError("请先在知识库创建或编辑中绑定一个爆款创作类型")

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
                "ViralAssetPreparationResultV2",
                ContractDomainContext(viral_source=source.model_dump()),
                context,
            )
            context._content_node_result_collector = collector
            context._content_node_output_contract = "ViralAssetPreparationResultV2"
            context._content_node_result_tool_name = "submit_content_node_result"
            context._content_node_max_tool_calls = 1
            context._content_node_token_budget = 48000
            context._content_node_tool_scope = ["submit_content_node_result"]
            payload = {
                "source": source.model_dump(),
                "source_hash": source.source_hash,
                "allowed_variable_codes": allowed_variable_codes,
                "kb_content_type_code": kb_content_type_code,
                "kb_content_type_name": CREATION_TYPE_NAMES.get(kb_content_type_code, kb_content_type_code),
            }
            runtime_snapshot = {
                "model": str(getattr(context, "model", "")),
                "skills": getattr(context, "_runtime_skill_snapshots", []) or [],
                "preparation_skill_hash": asset.preparation_skill_hash,
                "kb_content_type_code": kb_content_type_code,
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
        async with asyncio.timeout(900):
            await graph.ainvoke(
                {"messages": [json.dumps(payload, ensure_ascii=False)]},
                context=context,
                config={"configurable": {"thread_id": context.thread_id, "uid": context.uid}, "recursion_limit": 48},
            )
        result = validate_prepared_asset_v2(
            collector.finalize(),
            source,
            allowed_variable_codes=set(allowed_variable_codes),
        )
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
                    card = result.reference_card
                    issues = list(result.issues)
                    if (
                        source.industry_slug == "decoration"
                        and card is not None
                        and card.content_type_code
                        and card.content_type_code != kb_content_type_code
                    ):
                        issues.append(
                            f"识别类型 {card.content_type_code} 与知识库绑定 {kb_content_type_code}"
                            f"（{CREATION_TYPE_NAMES.get(kb_content_type_code, kb_content_type_code)}）不一致，"
                            "请核对原文是否属于本库创作类型"
                        )
                        result = result.model_copy(update={"status": "needs_review", "issues": issues})
                    asset.prepared_json = {
                        **result.model_dump(mode="json"),
                        "runtime_config_snapshot": runtime_snapshot,
                        "interrupt_count": 0,
                    }
                    # 新资产必须经运营审核后才进入在线参考池。
                    asset.status = "needs_review"
                    asset.error_message = "；".join(issues) or "等待运营审核"
            await AgentRunRepository(db).set_terminal_status(run_id, status="completed")
            await db.commit()
    except (Exception, asyncio.CancelledError) as exc:
        requeue = False
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
            message = _failure_message(exc)
            if asset and asset.status == "running" and asset.attempt == attempt:
                if isinstance(exc, (TimeoutError, asyncio.CancelledError)):
                    count = _interrupt_count(asset) + 1
                    prepared = dict(asset.prepared_json or {})
                    prepared["interrupt_count"] = count
                    asset.prepared_json = prepared
                    if count <= MAX_INTERRUPT_RETRIES:
                        asset.status = "pending"
                        asset.attempt = attempt + 1
                        asset.error_message = (
                            f"{message}，自动重试 {count}/{MAX_INTERRUPT_RETRIES}"
                        )
                        requeue = True
                    else:
                        asset.status = "failed"
                        asset.error_message = f"{message}（已中断重试 {MAX_INTERRUPT_RETRIES} 次）"
                else:
                    asset.status, asset.error_message = "failed", message
            if run_id:
                await AgentRunRepository(db).set_terminal_status(
                    run_id,
                    status="failed",
                    error_type="viral_asset_preparation_failed",
                    error_message=message,
                )
            await db.commit()
            if requeue and asset is not None:
                await enqueue_asset(db, asset)
        if isinstance(exc, asyncio.CancelledError):
            raise
