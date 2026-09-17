"""知识库文件的自动参考登记；文件任务与文章准备状态分离。"""

import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from yuxi.content.catalog import INDUSTRY_CONFIG
from yuxi.content.model.viral_document import document_units, xlsx_document_units
from yuxi.services.content_viral_assets import (
    accessible_asset_kbs,
    file_version,
    preparation_skill_hash,
    require_viral_kb_type,
)
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_content import ContentViralArticleVersion, ContentViralFileJob
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile


def detection_skill_hash():
    path = Path(__file__).parents[1] / "agents/skills/buildin/viral-document-detector/SKILL.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def read_parsed_document(file):
    from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
    from yuxi.storage.minio import get_minio_client

    if not file.markdown_file or not is_minio_url(file.markdown_file):
        raise HTTPException(409, "文件尚未解析出完整原文")
    bucket, name = parse_minio_url(file.markdown_file)
    return (await get_minio_client().adownload_file(bucket, name)).decode("utf-8")


async def read_reference_document(file):
    from yuxi.knowledge.utils.kb_utils import parse_minio_url
    from yuxi.storage.minio import get_minio_client

    if not file.markdown_file or file.status in {"uploaded", "parsing", "error_parsing"}:
        raise HTTPException(409, "文件尚未解析完成")
    extension = Path(file.filename).suffix.lower()
    if extension in {".xlsx", ".csv"}:
        # 通用 Markdown 转换会合并单元格换行，参考原文直接读取原文件。
        bucket, name = parse_minio_url(file.minio_url or file.path)
        data = await get_minio_client().adownload_file(bucket, name)
        units = (
            await asyncio.to_thread(xlsx_document_units, data)
            if extension == ".xlsx"
            else document_units(data.decode("utf-8-sig"), file.filename)
        )
        content = json.dumps(units, ensure_ascii=False, separators=(",", ":"))
    else:
        content = await read_parsed_document(file)
        units = document_units(content, file.filename)
    return content, units


def file_job_dict(job):
    return {
        key: getattr(job, key)
        for key in (
            "id",
            "kb_id",
            "file_id",
            "filename",
            "status",
            "error_message",
            "result_json",
            "agent_run_id",
            "attempt",
        )
    }


async def enqueue_file_job(db, job):
    try:
        pool = await get_arq_pool()
        await pool.enqueue_job(
            "process_viral_document", job.id, job.attempt, _job_id=f"viral-file:{job.id}:{job.attempt}"
        )
    except Exception as exc:
        job.status, job.error_message = "failed", f"准备任务入队失败：{exc}"
        await db.commit()
        raise


async def schedule_reference_file(db, user, kb_id, file_id, *, retry=False):
    from yuxi import knowledge_base

    if kb_id not in await accessible_asset_kbs(user):
        raise HTTPException(404, "知识库不存在或无权访问")
    await require_viral_kb_type(db, kb_id)
    file = (
        await db.execute(
            select(KnowledgeFile).where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.file_id == file_id,
            )
        )
    ).scalar_one_or_none()
    if file is None or file.is_folder:
        raise HTTPException(404, "原文文件不存在")
    content, units = await read_reference_document(file)
    version = file_version(file)
    if not content.strip() or not version:
        raise HTTPException(409, "文件尚未解析出完整原文")
    kb = (await db.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))).scalar_one()
    source_hash = hashlib.sha256(content.encode()).hexdigest()
    hint = (kb.additional_params or {}).get("industry_slug")
    if hint and hint not in INDUSTRY_CONFIG:
        raise HTTPException(422, "知识库行业配置无效，请修正后准备")
    skills = detection_skill_hash() + preparation_skill_hash()
    identity = f"{kb_id}:{file_id}:{version}:{source_hash}:{skills}:{hint}"
    job_id = "vfj_" + hashlib.sha256(identity.encode()).hexdigest()[:56]
    document = {
        "filename": file.filename,
        "source_hash": source_hash,
        "preparation_skill_hash": preparation_skill_hash(),
        "industry_hint": hint,
        "industries": list(INDUSTRY_CONFIG),
        "knowledge_base": {"name": kb.name, "description": kb.description},
        "units": units,
    }
    too_large = sum(len(unit["text"]) for unit in units) > 120_000
    await db.execute(
        insert(ContentViralFileJob)
        .values(
            id=job_id,
            kb_id=kb_id,
            file_id=file_id,
            filename=file.filename,
            file_version=version,
            source_hash=source_hash,
            skill_hash=detection_skill_hash(),
            input_json=document,
            result_json={},
            status="needs_review" if too_large else "pending",
            error_message="单文件原文超过 12 万字符，请按完整文章分文件后准备" if too_large else None,
            created_by=str(user.uid),
            attempt=1,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await db.execute(
        update(ContentViralFileJob)
        .where(
            ContentViralFileJob.file_id == file_id,
            ContentViralFileJob.id != job_id,
            ContentViralFileJob.status != "invalidated",
        )
        .values(status="invalidated")
    )
    await db.execute(
        update(ContentViralArticleVersion)
        .where(
            ContentViralArticleVersion.file_id == file_id,
            ContentViralArticleVersion.source_json["full_source_hash"].as_string() != source_hash,
        )
        .values(status="invalidated")
    )
    job = (
        await db.execute(select(ContentViralFileJob).where(ContentViralFileJob.id == job_id).with_for_update())
    ).scalar_one()
    if retry and job.status in {"failed", "needs_review"} and not too_large:
        job.status, job.error_message, job.attempt = "pending", None, job.attempt + 1
    await db.commit()
    # 通过知识库接口保存用途，避免内存元数据下次持久化时覆盖数据库字段。
    await knowledge_base.update_file_params(kb_id, file_id, {"use_as_viral_reference": True}, operator_id=str(user.uid))
    if job.status == "pending":
        await enqueue_file_job(db, job)
    return file_job_dict(job)


async def list_reference_file_jobs(db, user):
    jobs = list(
        (
            await db.execute(
                select(ContentViralFileJob)
                .where(
                    ContentViralFileJob.kb_id.in_(await accessible_asset_kbs(user)),
                    ContentViralFileJob.status != "invalidated",
                )
                .order_by(ContentViralFileJob.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )
    files = {
        file.file_id: file
        for file in (
            await db.execute(select(KnowledgeFile).where(KnowledgeFile.file_id.in_([job.file_id for job in jobs])))
        ).scalars()
    }
    asset_ids = [asset_id for job in jobs for asset_id in (job.result_json or {}).get("asset_ids", [])]
    assets_by_id = {
        asset.id: asset
        for asset in (
            await db.execute(select(ContentViralArticleVersion).where(ContentViralArticleVersion.id.in_(asset_ids)))
        ).scalars()
    }
    output = []
    for job in jobs:
        file = files.get(job.file_id)
        if file is None or file_version(file) != job.file_version:
            job.status, job.error_message = "invalidated", "原文已更新或删除"
        elif (
            job.skill_hash != detection_skill_hash()
            or job.input_json.get("preparation_skill_hash") != preparation_skill_hash()
        ):
            job.status, job.error_message = "invalidated", "参考准备标准已更新，请重新准备"
        value = file_job_dict(job)
        ids = (job.result_json or {}).get("asset_ids", [])
        assets = [assets_by_id[asset_id] for asset_id in ids if asset_id in assets_by_id]
        value["ready_count"] = sum(asset.status == "ready" for asset in assets)
        value["article_count"] = len(ids)
        value["preparing_count"] = sum(asset.status in {"pending", "running"} for asset in assets)
        output.append(value)
    await db.commit()
    return {"items": output}
