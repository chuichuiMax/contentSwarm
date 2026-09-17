"""异步识别文件中的文章，并复用单篇参考资产准备队列。"""

import asyncio
import hashlib
import json
import uuid

from sqlalchemy import select, update

from yuxi.agents.buildin import agent_manager
from yuxi.agents.context import prepare_agent_runtime_context
from yuxi.content.model.contracts import ContentNodeResultCollector, ContractDomainContext
from yuxi.content.model.viral_assets import ViralArticleSource
from yuxi.content.model.viral_document import extract_detected_articles, validate_document_result
from yuxi.repositories.agent_repository import AgentRepository
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.repositories.viral_asset_repository import ViralAssetRepository
from yuxi.services.agent_runtime_service import resolve_agent_runtime_context
from yuxi.services.content_viral_assets import accessible_asset_kbs, enqueue_asset, file_version, preparation_skill_hash
from yuxi.services.viral_document_service import detection_skill_hash, read_reference_document
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentViralArticleVersion, ContentViralFileJob
from yuxi.storage.postgres.models_knowledge import KnowledgeFile


async def assert_current_document(db, job, user):
    if user is None or job.kb_id not in await accessible_asset_kbs(user):
        raise ValueError("参考原文访问权限已失效")
    file = (await db.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == job.file_id))).scalar_one_or_none()
    if file is None or file_version(file) != job.file_version or job.skill_hash != detection_skill_hash():
        raise ValueError("原文或识别标准已变化，请重新准备")
    content, _ = await read_reference_document(file)
    if hashlib.sha256(content.encode()).hexdigest() != job.source_hash:
        raise ValueError("解析原文已变化，请重新准备")


async def process_viral_document(_ctx, job_id, attempt):
    run_id = None
    try:
        async with pg_manager.get_async_session_context() as db:
            job = (
                await db.execute(
                    select(ContentViralFileJob)
                    .where(
                        ContentViralFileJob.id == job_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None or job.status != "pending" or job.attempt != attempt:
                return
            job.status = "running"
            await db.commit()
            user = (
                await db.execute(select(User).where(User.uid == job.created_by, User.is_deleted == 0))
            ).scalar_one_or_none()
            await assert_current_document(db, job, user)
            document = job.input_json
            context = await resolve_agent_runtime_context(db=db, user=user, bound_agent_id="content-viral-asset-agent")
            agent = await AgentRepository(db).get_visible_by_slug(slug="content-viral-asset-agent", user=user)
            backend = agent_manager.get_agent(agent.backend_id)
            run_id = f"run_{uuid.uuid4().hex}"
            context.thread_id, context.run_id = f"viral-document:{uuid.uuid4().hex}", run_id
            context.request_id = f"viral-document:{uuid.uuid4().hex}"
            context.required_skills, context.knowledges = ["viral-document-detector"], []
            await prepare_agent_runtime_context(context, context_schema=backend.context_schema)
            collector = ContentNodeResultCollector(
                "ViralDocumentResultV1", ContractDomainContext(viral_document=document), context
            )
            context._content_node_result_collector = collector
            context._content_node_output_contract = "ViralDocumentResultV1"
            context._content_node_result_tool_name = "submit_content_node_result"
            context._content_node_max_tool_calls = 1
            context._content_node_token_budget = 10000
            context._content_node_tool_scope = ["submit_content_node_result"]
            runs = AgentRunRepository(db)
            await runs.create_run(
                run_id=run_id,
                thread_id=context.thread_id,
                agent_id=agent.slug,
                uid=str(user.uid),
                request_id=context.request_id,
                run_type="viral_document_detection",
                input_payload={
                    "job_id": job.id,
                    "document": document,
                    "skill_hash": job.skill_hash,
                    "skills": getattr(context, "_runtime_skill_snapshots", []),
                },
            )
            await runs.mark_running(run_id)
            job.agent_run_id = run_id
            await db.commit()
        graph = await backend.get_graph(context=context)
        async with asyncio.timeout(140):
            await graph.ainvoke(
                {"messages": [json.dumps({"document": document}, ensure_ascii=False)]},
                context=context,
                config={"configurable": {"thread_id": context.thread_id, "uid": context.uid}, "recursion_limit": 12},
            )
        result = validate_document_result(collector.finalize(), document)
        async with pg_manager.get_async_session_context() as db:
            job = (
                await db.execute(select(ContentViralFileJob).where(ContentViralFileJob.id == job_id).with_for_update())
            ).scalar_one()
            user = (
                await db.execute(select(User).where(User.uid == job.created_by, User.is_deleted == 0))
            ).scalar_one_or_none()
            await assert_current_document(db, job, user)
            if job.status != "running" or job.attempt != attempt:
                await AgentRunRepository(db).set_terminal_status(run_id, status="cancelled")
                await db.commit()
                return
            # 本文件的重新识别替换候选集合，历史原文仍保留。
            await db.execute(
                update(ContentViralArticleVersion)
                .where(
                    ContentViralArticleVersion.file_id == job.file_id,
                )
                .values(status="invalidated")
            )
            assets = []
            for article in extract_detected_articles(result, document):
                source = ViralArticleSource(
                    kb_id=job.kb_id,
                    file_id=job.file_id,
                    **article,
                    full_source_hash=job.source_hash,
                    source_file_version=job.file_version,
                    completeness="complete",
                    viral_basis="用户选择用于仿写参考；未据此认定平台传播效果",
                )
                asset = await ViralAssetRepository(db).register(
                    source, skill_hash=preparation_skill_hash(), uid=str(user.uid)
                )
                # 同一原文重识别时复用已核验结果，只恢复刚被集合替换失效的同版本资产。
                if asset.status == "invalidated":
                    asset.status = "ready" if (asset.prepared_json or {}).get("status") == "prepared" else "pending"
                    if asset.status == "pending":
                        asset.attempt += 1
                assets.append(asset)
            job.result_json = {**result.model_dump(), "asset_ids": [asset.id for asset in assets]}
            job.status = "needs_review" if result.issues else "completed"
            job.error_message = "；".join(result.issues) or None
            await AgentRunRepository(db).set_terminal_status(run_id, status="completed")
            await db.commit()
            for asset in assets:
                if asset.status == "pending":
                    await enqueue_asset(db, asset)
    except (Exception, asyncio.CancelledError) as exc:
        async with pg_manager.get_async_session_context() as db:
            job = (
                await db.execute(select(ContentViralFileJob).where(ContentViralFileJob.id == job_id).with_for_update())
            ).scalar_one_or_none()
            if job and job.attempt == attempt and job.status == "running":
                job.status, job.error_message = "failed", str(exc) or "自动准备已中断"
            if run_id:
                await AgentRunRepository(db).set_terminal_status(
                    run_id, status="failed", error_type="viral_document_failed", error_message=str(exc)
                )
            await db.commit()
        if isinstance(exc, asyncio.CancelledError):
            raise
