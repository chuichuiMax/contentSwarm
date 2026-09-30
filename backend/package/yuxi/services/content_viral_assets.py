"""爆款文章入库、准备及读取；不在在线生成链路内补建画像。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import case, func, literal, or_, select

from yuxi.content.catalog import INDUSTRY_CONFIG
from yuxi.content.model.viral_assets import (
    ViralArticleSource,
    ViralAssetCorrectionInput,
    ViralAssetImport,
    ViralAssetReviewInput,
    extract_article_records,
    validate_prepared_asset_v2,
)
from yuxi.repositories.viral_asset_repository import ViralAssetRepository, asset_dict
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.postgres.models_content import (
    ContentRuleVersion,
    ContentViralArticleVersion,
    ContentViralFileJob,
    VariableDefinition,
)
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile
from yuxi.utils.datetime_utils import utc_now_naive


def preparation_skill_hash() -> str:
    path = Path(__file__).parents[1] / "agents/skills/buildin/viral-asset-preparer/SKILL.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def published_variable_codes(db) -> set[str]:
    return set(
        (
            await db.execute(
                select(VariableDefinition.code)
                .join(ContentRuleVersion, ContentRuleVersion.id == VariableDefinition.rule_version_id)
                .where(
                    ContentRuleVersion.status == "published",
                    VariableDefinition.enabled.is_(True),
                )
            )
        ).scalars()
    )


def reference_card_variable_codes(prepared_json: dict) -> set[str]:
    card = (prepared_json or {}).get("reference_card") or {}
    return {str(code) for slot in card.get("required_slots") or [] for code in slot.get("variable_codes") or [] if code}


def asset_has_approved_review(asset: ContentViralArticleVersion) -> bool:
    prepared = asset.prepared_json or {}
    review_action = (prepared.get("review") or {}).get("action")
    return prepared.get("status") == "prepared" and review_action in {"approve", "enable"}


async def accessible_asset_kbs(user) -> list[str]:
    from yuxi import knowledge_base

    databases = await knowledge_base.get_databases_by_uid(user.uid)
    return [item["kb_id"] for item in databases.get("databases", [])]


async def require_viral_kb_type(db, kb_id: str) -> str:
    content_type = await db.scalar(
        select(KnowledgeBase.additional_params["viral_content_type"].as_string()).where(KnowledgeBase.kb_id == kb_id)
    )
    if content_type not in tuple(f"CT{i:02d}" for i in range(1, 8)):
        raise HTTPException(422, "请先在知识库创建或编辑中绑定一个爆款创作类型")
    return content_type


def file_version(file: KnowledgeFile) -> str:
    return str(file.content_hash or (file.updated_at.isoformat() if file.updated_at else ""))


async def require_asset(db, user, asset_id: str, *, for_update: bool = False):
    asset = await ViralAssetRepository(db).get(asset_id, kb_ids=await accessible_asset_kbs(user), for_update=for_update)
    if asset is None:
        raise HTTPException(404, "爆款资产不存在或无权访问")
    return asset


async def check_asset_source(db, asset: ContentViralArticleVersion) -> bool:
    file = (
        await db.execute(
            select(KnowledgeFile).where(
                KnowledgeFile.file_id == asset.file_id,
                KnowledgeFile.kb_id == asset.kb_id,
            )
        )
    ).scalar_one_or_none()
    return bool(file and file_version(file) == asset.source_json["source_file_version"])


async def search_ready_viral_assets(
    db,
    user,
    *,
    industry_slug: str,
    query: str,
    kb_ids: list[str] | None = None,
    limit: int,
    include_structure: bool = False,
    content_type_code: str | None = None,
):
    """按类型发现权限内的参考库；事实相关度用于同类文章排序，最终适配由选择 Agent 判断。"""
    accessible = set(await accessible_asset_kbs(user))
    allowed = sorted(accessible if kb_ids is None else accessible & set(kb_ids))
    if not allowed:
        return []
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.lower())
    terms = list(
        dict.fromkeys(
            term
            for word in words
            for term in ([word] if word.isascii() or len(word) < 2 else [word[i : i + 2] for i in range(len(word) - 1)])
        )
    )[:64]
    if not terms and not content_type_code:
        return []
    asset = ContentViralArticleVersion
    card = asset.prepared_json["reference_card"]
    search_text = func.lower(
        func.concat(
            asset.source_json["title"].as_string(),
            " ",
            card["summary"].as_string(),
            " ",
            card["audience"].as_string(),
            " ",
            card["scene"].as_string(),
            " ",
            card["goal"].as_string(),
            *(
                [
                    " ",
                    asset.prepared_json["reference_blueprint"]["content_block_sequence"].as_string(),
                    " ",
                    asset.prepared_json["reference_blueprint"]["narrative_structure"].as_string(),
                ]
                if include_structure
                else []
            ),
        )
    )
    relevance = sum((case((search_text.contains(term, autoescape=True), 1), else_=0) for term in terms), literal(0))
    rows = list(
        (
            await db.execute(
                select(asset)
                .join(KnowledgeFile, (KnowledgeFile.file_id == asset.file_id) & (KnowledgeFile.kb_id == asset.kb_id))
                .join(KnowledgeBase, KnowledgeBase.kb_id == asset.kb_id)
                .where(
                    KnowledgeBase.additional_params["viral_content_type"].as_string()
                    == card["content_type_code"].as_string(),
                    asset.kb_id.in_(allowed),
                    asset.industry_slug == industry_slug,
                    asset.status == "ready",
                    card["schema_version"].as_integer() == 2,
                    *([card["content_type_code"].as_string() == content_type_code] if content_type_code else []),
                    asset.preparation_skill_hash == preparation_skill_hash(),
                    or_(
                        KnowledgeFile.content_hash.is_(None),
                        KnowledgeFile.content_hash == "",
                        KnowledgeFile.content_hash == asset.source_json["source_file_version"].as_string(),
                    ),
                    *([] if content_type_code else [relevance > 0]),
                )
                .order_by(relevance.desc(), asset.id)
                .limit(limit * 2)
            )
        ).scalars()
    )
    if not rows:
        return []
    allowed_variable_codes = await published_variable_codes(db)
    result = []
    for rank, row in enumerate(rows):
        if not asset_has_approved_review(row):
            row.status, row.error_message = "needs_review", "等待运营审核"
            continue
        if not await check_asset_source(db, row):
            row.status, row.error_message = "invalidated", "原文已更新，请重新准备"
            continue
        if row.preparation_skill_hash != preparation_skill_hash():
            continue
        if reference_card_variable_codes(row.prepared_json) - allowed_variable_codes:
            row.status, row.error_message = "invalidated", "参考槽位引用的变量已停用，请重新准备"
            continue
        item = asset_dict(row)
        card = item["reference_card"]
        item["reference_card"] = {
            **{
                key: card[key]
                for key in (
                    "schema_version",
                    "audience",
                    "scene",
                    "goal",
                    "channel",
                    "summary",
                    "content_type_code",
                    "content_type_reason",
                )
            },
            "required_slots": [
                {
                    key: slot[key]
                    for key in (
                        "slot_key",
                        "name",
                        "description",
                        "variable_codes",
                        "match_mode",
                        "evidence_required",
                        "required",
                    )
                }
                for slot in card["required_slots"]
            ],
        }
        result.append(
            {
                key: item[key]
                for key in (
                    "id",
                    "article_id",
                    "kb_id",
                    "file_id",
                    "industry_slug",
                    "source_hash",
                    "preparation_skill_hash",
                    "title",
                    "locator",
                    "reference_card",
                )
            }
        )
        result[-1]["retrieval_score"] = len(rows) - rank
        if include_structure:
            result[-1]["structure_preview"] = {
                key: row.prepared_json["reference_blueprint"][key]
                for key in ("content_block_sequence", "narrative_structure")
            }
        if len(result) == limit:
            break
    return result


async def enqueue_asset(db, asset):
    await db.commit()
    try:
        queue = await get_arq_pool()
        await queue.enqueue_job(
            "process_viral_asset", asset.id, asset.attempt, _job_id=f"viral-asset:{asset.id}:{asset.attempt}"
        )
    except Exception as exc:
        asset.status, asset.error_message = "failed", "爆款资产准备队列不可用"
        await db.commit()
        raise HTTPException(503, asset.error_message) from exc


async def import_viral_assets(db, user, payload: ViralAssetImport):
    from yuxi import knowledge_base

    if payload.kb_id not in await accessible_asset_kbs(user):
        raise HTTPException(404, "知识库不存在或无权访问")
    await require_viral_kb_type(db, payload.kb_id)
    if payload.industry_slug not in INDUSTRY_CONFIG:
        raise HTTPException(422, "行业不存在")
    file = (
        await db.execute(
            select(KnowledgeFile).where(
                KnowledgeFile.file_id == payload.file_id,
                KnowledgeFile.kb_id == payload.kb_id,
            )
        )
    ).scalar_one_or_none()
    if file is None or file.is_folder:
        raise HTTPException(404, "源文件不存在")
    version = file_version(file)
    preview = await knowledge_base.read_file_preview(payload.kb_id, payload.file_id, variant="parsed")
    content = preview.get("content")
    if not preview.get("supported") or not isinstance(content, str) or not content.strip() or not version:
        raise HTTPException(409, "源文件尚无可核验完整原文")
    try:
        records = extract_article_records(
            content, layout=payload.layout, title_column=payload.title_column, body_column=payload.body_column
        )
        if len(records) > 100:
            raise ValueError("单次最多准备 100 篇文章，请分文件处理")
        sources = [
            ViralArticleSource(
                kb_id=payload.kb_id,
                file_id=payload.file_id,
                industry_slug=payload.industry_slug,
                **record,
                full_source_hash=hashlib.sha256(content.encode()).hexdigest(),
                source_file_version=version,
                completeness="complete",
                viral_basis=f"管理员 {user.uid} 入库确认：{payload.viral_basis}",
            )
            for record in records
        ]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.refresh(file)
    if file_version(file) != version:
        raise HTTPException(409, "读取期间源文件已更新，请重新导入")
    repo = ViralAssetRepository(db)
    assets = [await repo.register(source, skill_hash=preparation_skill_hash(), uid=str(user.uid)) for source in sources]
    await db.commit()
    for asset in assets:
        if asset.status == "pending":
            await enqueue_asset(db, asset)
    return {"items": [asset_dict(asset) for asset in assets]}


async def list_viral_assets(db, user, *, industry_slug=None, ready_only=False, content_type_code=None, limit=100):
    assets = await ViralAssetRepository(db).list(
        kb_ids=await accessible_asset_kbs(user),
        industry_slug=industry_slug,
        ready_only=ready_only,
        content_type_code=content_type_code,
        limit=limit,
    )
    allowed_variable_codes = await published_variable_codes(db)
    for asset in assets:
        if asset.status == "ready" and not await check_asset_source(db, asset):
            asset.status, asset.error_message = "invalidated", "源文件已更新或删除，请重新准备"
        elif asset.status == "ready" and asset.preparation_skill_hash != preparation_skill_hash():
            asset.status, asset.error_message = "invalidated", "准备标准已更新，请重新导入"
        elif asset.status == "ready" and (asset.prepared_json or {}).get("schema_version") != 2:
            asset.status, asset.error_message = "invalidated", "参考卡契约已升级，请重新准备"
        elif asset.status == "ready" and reference_card_variable_codes(asset.prepared_json) - allowed_variable_codes:
            asset.status, asset.error_message = "invalidated", "参考槽位引用的变量已停用，请重新准备"
        elif asset.status == "ready" and not asset_has_approved_review(asset):
            asset.status, asset.error_message = "needs_review", "等待运营审核"
    await db.commit()
    return {"items": [asset_dict(asset) for asset in assets if not ready_only or asset.status == "ready"]}


async def retry_viral_asset(db, user, asset_id: str):
    asset = await require_asset(db, user, asset_id, for_update=True)
    if asset.status not in {"failed", "needs_review"}:
        raise HTTPException(409, "只有失败或待核验资产可以重试")
    if not await check_asset_source(db, asset):
        raise HTTPException(409, "源文件已更新，请重新导入完整文章")
    if asset.preparation_skill_hash != preparation_skill_hash():
        raise HTTPException(409, "准备 Skill 已更新，请重新导入以建立新版本")
    asset.status, asset.error_message = "pending", None
    asset.attempt += 1
    await enqueue_asset(db, asset)
    return {"asset": asset_dict(asset)}


async def review_viral_asset(db, user, asset_id: str, payload: ViralAssetReviewInput):
    asset = await require_asset(db, user, asset_id, for_update=True)
    prepared = dict(asset.prepared_json or {})
    if payload.action in {"approve", "enable"}:
        if payload.action == "approve" and asset.status != "needs_review":
            raise HTTPException(409, "只有待审核资产可以通过审核")
        if payload.action == "enable" and asset.status != "invalidated":
            raise HTTPException(409, "只有已停用资产可以重新启用")
        if not await check_asset_source(db, asset) or asset.preparation_skill_hash != preparation_skill_hash():
            raise HTTPException(409, "原文或准备标准已变化，请重新准备")
        try:
            allowed_variable_codes = await published_variable_codes(db)
            if not allowed_variable_codes:
                raise HTTPException(409, "已发布变量目录为空，不能发布爆款资产")
            contract_payload = {
                key: prepared[key]
                for key in (
                    "schema_version",
                    "status",
                    "source_hash",
                    "reference_card",
                    "reference_blueprint",
                    "blueprint_anchors",
                    "issues",
                )
                if key in prepared
            }
            result = validate_prepared_asset_v2(
                contract_payload,
                ViralArticleSource.model_validate(asset.source_json),
                allowed_variable_codes=allowed_variable_codes,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if result.status != "prepared":
            raise HTTPException(409, "准备结果仍有未解决问题，不能发布")
        asset.status, asset.error_message = "ready", None
    elif payload.action == "reject":
        if asset.status != "needs_review":
            raise HTTPException(409, "只有待审核资产可以驳回")
        asset.status, asset.error_message = "needs_review", payload.reason
    else:
        if asset.status != "ready":
            raise HTTPException(409, "只有已发布资产可以停用")
        asset.status, asset.error_message = "invalidated", payload.reason or "运营已停用"
    review_entry = {
        "action": payload.action,
        "reason": payload.reason,
        "reviewed_by": str(user.uid),
        "reviewed_at": utc_now_naive().isoformat(),
    }
    prepared["review"] = review_entry
    prepared["review_history"] = [*(prepared.get("review_history") or []), review_entry]
    asset.prepared_json = prepared
    await db.commit()
    return {"asset": asset_dict(asset, include_source=True)}


async def correct_viral_asset(db, user, asset_id: str, payload: ViralAssetCorrectionInput):
    asset = await require_asset(db, user, asset_id, for_update=True)
    if asset.status != "needs_review":
        raise HTTPException(409, "只能修正待审核资产；已发布资产请先停用并重新准备")
    if not await check_asset_source(db, asset) or asset.preparation_skill_hash != preparation_skill_hash():
        raise HTTPException(409, "原文或准备标准已变化，请重新准备")
    prepared = dict(asset.prepared_json or {})
    card = dict(prepared.get("reference_card") or {})
    existing_slots = list(card.get("required_slots") or [])
    if len(existing_slots) != len(payload.required_slots):
        raise HTTPException(422, "为保留原文锚点，修正时不能新增或删除槽位，请重新准备")
    card.update(
        content_type_code=payload.content_type_code,
        content_type_reason=payload.content_type_reason,
        required_slots=[
            {**slot.model_dump(mode="json"), "anchor": existing_slots[index]["anchor"]}
            for index, slot in enumerate(payload.required_slots)
        ],
    )
    prepared["reference_card"] = card
    contract_payload = {
        key: prepared[key]
        for key in (
            "schema_version",
            "status",
            "source_hash",
            "reference_card",
            "reference_blueprint",
            "blueprint_anchors",
            "issues",
        )
        if key in prepared
    }
    try:
        validate_prepared_asset_v2(
            contract_payload,
            ViralArticleSource.model_validate(asset.source_json),
            allowed_variable_codes=await published_variable_codes(db),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    review_entry = {
        "action": "correct",
        "reason": payload.reason,
        "reviewed_by": str(user.uid),
        "reviewed_at": utc_now_naive().isoformat(),
    }
    prepared["review"] = review_entry
    prepared["review_history"] = [*(prepared.get("review_history") or []), review_entry]
    asset.prepared_json = prepared
    asset.error_message = "运营已修正类型或槽位，等待审核发布"
    await db.commit()
    return {"asset": asset_dict(asset, include_source=True)}


async def reprepare_viral_assets(db, user, asset_ids: list[str]):
    repo = ViralAssetRepository(db)
    queued = []
    for asset_id in dict.fromkeys(asset_ids):
        asset = await require_asset(db, user, asset_id)
        if not await check_asset_source(db, asset):
            raise HTTPException(409, f"资产 {asset_id} 原文已更新，请从源文件重新识别")
        source = ViralArticleSource.model_validate(asset.source_json)
        queued.append(await repo.register(source, skill_hash=preparation_skill_hash(), uid=str(user.uid)))
    await db.commit()
    for asset in queued:
        if asset.status == "pending":
            await enqueue_asset(db, asset)
    return {"items": [asset_dict(asset) for asset in queued]}


async def delete_viral_asset(db, user, asset_id: str):
    asset = await require_asset(db, user, asset_id, for_update=True)
    if asset.status == "running":
        raise HTTPException(409, "资产正在准备中，请稍后再删除")
    if asset.status == "ready":
        raise HTTPException(409, "已发布资产不能删除")
    if asset.status not in {"pending", "failed", "needs_review"}:
        raise HTTPException(409, "只有等待准备、准备失败或待处理资产可以删除")

    jobs = list(
        (await db.execute(select(ContentViralFileJob).where(ContentViralFileJob.file_id == asset.file_id))).scalars()
    )
    for job in jobs:
        result = dict(job.result_json or {})
        asset_ids = list(result.get("asset_ids") or [])
        if asset_id in asset_ids:
            result["asset_ids"] = [item for item in asset_ids if item != asset_id]
            job.result_json = result

    await db.delete(asset)
    await db.commit()
    return {"success": True, "id": asset_id}
