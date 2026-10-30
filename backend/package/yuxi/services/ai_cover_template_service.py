"""管理员新增的 AI 封面模板。"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.content_cover.ai_cover_prompt import AiCoverJobSelection, select_ai_cover_prompt
from yuxi.content_cover.ai_cover_prompt_catalog import list_ai_cover_manage_catalog, specs_for_content_type
from yuxi.services.content_type_service import ensure_default_content_types
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import AiCoverCatalogOverride, AiCoverTemplate, ContentType

_CUSTOM_ID = re.compile(r"^c[0-9a-f]{16}$")
_ALLOWED_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_PROMPT_CHARS = 20000


def is_custom_cover_id(variant: str | None) -> bool:
    return bool(_CUSTOM_ID.fullmatch((variant or "").strip().lower()))


def cover_upload_name(filename: str | None) -> str:
    name = Path(str(filename or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or "/" in name:
        raise _cover_error(422, "AI_COVER_FILE_INVALID", "请上传封面模板图片")
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise _cover_error(422, "AI_COVER_FILE_TYPE", "封面模板仅支持 PNG、JPG、WebP")
    return name


def _cover_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": {"code": code, "message": message}})


def _clean_text(value: str, *, field: str, limit: int) -> str:
    text = value.strip()
    if not text:
        raise _cover_error(422, "AI_COVER_FIELD_REQUIRED", f"请输入{field}")
    if len(text) > limit:
        raise _cover_error(422, "AI_COVER_FIELD_TOO_LONG", f"{field}不能超过 {limit} 个字")
    return text


async def list_custom_cover_prompts(db: AsyncSession, *, content_type_name: str = "") -> list[dict]:
    result = await db.execute(select(AiCoverTemplate).order_by(AiCoverTemplate.created_at.desc()))
    wanted = content_type_name.strip()
    prompts = []
    for row in result.scalars().all():
        names = list(row.content_type_names or [])
        if wanted and wanted not in names:
            continue
        prompts.append(row.to_catalog_dict())
    return prompts


def catalog_cover_variants() -> set[str]:
    return {spec.variant for spec in specs_for_content_type(content_type_name="装修案例分享")}


def merge_catalog_item(item: dict, override: AiCoverCatalogOverride | None) -> dict | None:
    if override is None:
        return item
    if override.hidden:
        return None
    merged = dict(item)
    if item.get("variant") == "smart-random":
        merged["label"] = override.name
    else:
        merged["title"] = override.name
    merged["prompt_body"] = override.prompt or ""
    merged["content_type_names"] = list(override.content_type_names or [])
    return merged


async def _override_map(db: AsyncSession) -> dict[str, AiCoverCatalogOverride]:
    result = await db.execute(select(AiCoverCatalogOverride))
    return {row.variant: row for row in result.scalars().all()}


async def hidden_catalog_variants(db: AsyncSession) -> frozenset[str]:
    result = await db.execute(
        select(AiCoverCatalogOverride.variant).where(AiCoverCatalogOverride.hidden.is_(True))
    )
    return frozenset(row[0] for row in result.all())


async def apply_catalog_overrides(
    db: AsyncSession,
    prompts: list[dict],
    *,
    content_type_name: str = "",
) -> list[dict]:
    overrides = await _override_map(db)
    wanted = content_type_name.strip()
    visible: list[dict] = []
    for item in prompts:
        merged = merge_catalog_item(item, overrides.get(str(item.get("variant") or "")))
        if merged is None:
            continue
        names = list(merged.get("content_type_names") or [])
        if wanted and overrides.get(str(item.get("variant") or "")) and wanted not in names:
            continue
        visible.append(merged)
    return visible


async def list_managed_covers(db: AsyncSession) -> dict:
    catalog = list_ai_cover_manage_catalog()["covers"]
    visible = await apply_catalog_overrides(db, catalog)
    custom = await list_custom_cover_prompts(db)
    return {"covers": [*custom, *visible]}


async def stored_cover_preview_bytes(db: AsyncSession, variant: str) -> tuple[bytes, str] | None:
    custom = await custom_cover_preview_bytes(db, variant)
    if custom:
        return custom
    cover_id = (variant or "").strip().lower()
    row = await db.get(AiCoverCatalogOverride, cover_id)
    if row is None or row.hidden or not row.image:
        return None
    return bytes(row.image), row.media_type or "image/png"


async def custom_cover_preview_bytes(db: AsyncSession, variant: str) -> tuple[bytes, str] | None:
    cover_id = (variant or "").strip().lower()
    if not is_custom_cover_id(cover_id):
        return None
    row = await db.get(AiCoverTemplate, cover_id)
    if row is None or not row.image:
        return None
    return bytes(row.image), row.media_type


async def selection_for_saved_cover(
    db: AsyncSession,
    variant: str | None,
    *,
    content_type_name: str = "",
) -> AiCoverJobSelection | None:
    cover_id = (variant or "").strip().lower()
    if not is_custom_cover_id(cover_id):
        return None
    row = await db.get(AiCoverTemplate, cover_id)
    if row is None:
        raise ValueError("封面模板不存在或已删除")
    names = list(row.content_type_names or [])
    if content_type_name.strip() and content_type_name.strip() not in names:
        raise ValueError("该封面模板未关联当前内容类型")
    return AiCoverJobSelection(prompt=row.prompt, variant=row.id, render_copy_with_image2=True)


async def create_ai_cover_template(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    prompt: str,
    content_type_names: list[str],
    file: UploadFile,
) -> dict:
    title = _clean_text(name, field="封面名称", limit=64)
    body = _clean_text(prompt, field="封面提示词", limit=_MAX_PROMPT_CHARS)
    names = _clean_content_types(content_type_names)
    file_name = cover_upload_name(file.filename)
    image = await file.read()
    if not image:
        raise _cover_error(422, "AI_COVER_FILE_INVALID", "请上传封面模板图片")
    if len(image) > _MAX_IMAGE_BYTES:
        raise _cover_error(422, "AI_COVER_FILE_TOO_LARGE", "封面模板不能超过 10 MB")
    await ensure_default_content_types(db)
    await _require_known_content_types(db, names)
    await _require_unique_file_name(db, file_name)
    suffix = Path(file_name).suffix.lower()
    row = AiCoverTemplate(
        id=f"c{uuid.uuid4().hex[:16]}",
        name=title,
        prompt=body,
        content_type_names=names,
        file_name=file_name,
        media_type=_ALLOWED_SUFFIXES[suffix],
        image=image,
        created_by=str(user.uid),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise _cover_error(409, "AI_COVER_DUPLICATE_FILE", "不可上传相同文件名称") from exc
    return {"cover": row.to_catalog_dict()}


def _clean_content_types(values: list[str]) -> list[str]:
    names: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in names:
            names.append(text)
    if not names:
        raise _cover_error(422, "AI_COVER_TYPE_REQUIRED", "请选择内容类型")
    return names


async def _require_known_content_types(db: AsyncSession, names: list[str]) -> None:
    result = await db.execute(select(ContentType.name).where(ContentType.name.in_(names)))
    known = {row[0] for row in result.all()}
    missing = [name for name in names if name not in known]
    if missing:
        raise _cover_error(422, "AI_COVER_TYPE_UNKNOWN", f"内容类型不存在：{'、'.join(missing)}")


async def _require_unique_file_name(
    db: AsyncSession,
    file_name: str,
    *,
    except_template_id: str | None = None,
    except_variant: str | None = None,
) -> None:
    template_query = select(AiCoverTemplate.id).where(func.lower(AiCoverTemplate.file_name) == file_name.lower())
    if except_template_id:
        template_query = template_query.where(AiCoverTemplate.id != except_template_id)
    if (await db.execute(template_query)).scalar_one_or_none():
        raise _cover_error(409, "AI_COVER_DUPLICATE_FILE", "不可上传相同文件名称")
    override_query = select(AiCoverCatalogOverride.variant).where(
        func.lower(AiCoverCatalogOverride.file_name) == file_name.lower()
    )
    if except_variant:
        override_query = override_query.where(AiCoverCatalogOverride.variant != except_variant)
    if (await db.execute(override_query)).scalar_one_or_none():
        raise _cover_error(409, "AI_COVER_DUPLICATE_FILE", "不可上传相同文件名称")


async def _read_replacement_image(
    db: AsyncSession,
    file: UploadFile | None,
    *,
    except_template_id: str | None = None,
    except_variant: str | None = None,
) -> tuple[str, str, bytes] | None:
    if file is None or not (file.filename or "").strip():
        return None
    file_name = cover_upload_name(file.filename)
    image = await file.read()
    if not image:
        raise _cover_error(422, "AI_COVER_FILE_INVALID", "请上传封面模板图片")
    if len(image) > _MAX_IMAGE_BYTES:
        raise _cover_error(422, "AI_COVER_FILE_TOO_LARGE", "封面模板不能超过 10 MB")
    await _require_unique_file_name(
        db,
        file_name,
        except_template_id=except_template_id,
        except_variant=except_variant,
    )
    suffix = Path(file_name).suffix.lower()
    return file_name, _ALLOWED_SUFFIXES[suffix], image


async def update_ai_cover_template(
    db: AsyncSession,
    cover_id: str,
    *,
    name: str,
    prompt: str,
    content_type_names: list[str],
    file: UploadFile | None = None,
) -> dict:
    title = _clean_text(name, field="封面名称", limit=64)
    body = _clean_text(prompt, field="封面提示词", limit=_MAX_PROMPT_CHARS)
    names = _clean_content_types(content_type_names)
    await ensure_default_content_types(db)
    await _require_known_content_types(db, names)
    cover_id = (cover_id or "").strip().lower()
    if is_custom_cover_id(cover_id):
        row = await db.get(AiCoverTemplate, cover_id)
        if row is None:
            raise _cover_error(404, "AI_COVER_NOT_FOUND", "封面不存在")
        replacement = await _read_replacement_image(db, file, except_template_id=row.id)
        row.name = title
        row.prompt = body
        row.content_type_names = names
        if replacement:
            row.file_name, row.media_type, row.image = replacement
        try:
            await db.flush()
        except IntegrityError as exc:
            raise _cover_error(409, "AI_COVER_DUPLICATE_FILE", "不可上传相同文件名称") from exc
        return {"cover": row.to_catalog_dict()}
    if cover_id not in catalog_cover_variants():
        raise _cover_error(404, "AI_COVER_NOT_FOUND", "封面不存在")
    replacement = await _read_replacement_image(db, file, except_variant=cover_id)
    override = await db.get(AiCoverCatalogOverride, cover_id)
    if override is None:
        override = AiCoverCatalogOverride(variant=cover_id, name=title, prompt=body, content_type_names=names)
        db.add(override)
    override.name = title
    override.prompt = body
    override.content_type_names = names
    override.hidden = False
    if replacement:
        override.file_name, override.media_type, override.image = replacement
    await db.flush()
    return {"cover": {"variant": cover_id}}


async def delete_ai_cover_template(db: AsyncSession, cover_id: str) -> dict:
    cover_id = (cover_id or "").strip().lower()
    if is_custom_cover_id(cover_id):
        row = await db.get(AiCoverTemplate, cover_id)
        if row is None:
            raise _cover_error(404, "AI_COVER_NOT_FOUND", "封面不存在")
        await db.delete(row)
        await db.flush()
        return {"success": True}
    if cover_id not in catalog_cover_variants():
        raise _cover_error(404, "AI_COVER_NOT_FOUND", "封面不存在")
    override = await db.get(AiCoverCatalogOverride, cover_id)
    if override is None:
        override = AiCoverCatalogOverride(
            variant=cover_id,
            name=cover_id,
            prompt="",
            content_type_names=[],
            hidden=True,
        )
        db.add(override)
    else:
        override.hidden = True
    await db.flush()
    return {"success": True}


async def catalog_override_selection(
    db: AsyncSession,
    variant: str | None,
    *,
    content_type_name: str = "",
) -> AiCoverJobSelection | None:
    cover_id = (variant or "").strip().lower()
    if not cover_id or is_custom_cover_id(cover_id):
        return None
    row = await db.get(AiCoverCatalogOverride, cover_id)
    if row is None or row.hidden or not (row.prompt or "").strip():
        return None
    names = list(row.content_type_names or [])
    if content_type_name.strip() and content_type_name.strip() not in names:
        raise ValueError("该封面模板未关联当前内容类型")
    return AiCoverJobSelection(prompt=row.prompt, variant=row.variant, render_copy_with_image2=True)


async def cover_generation_selection(
    db: AsyncSession,
    *,
    title: str,
    subtitle: str,
    form_values: dict | None,
    content_type_code: str,
    content_type_name: str,
    prompt_controls: dict,
) -> AiCoverJobSelection:
    saved = await selection_for_saved_cover(
        db,
        prompt_controls.get("prompt_variant"),
        content_type_name=content_type_name,
    )
    if saved:
        return saved
    selection = select_ai_cover_prompt(
        title=title,
        subtitle=subtitle,
        form_values=form_values,
        content_type_code=content_type_code,
        content_type_name=content_type_name,
        exclude_variants=await hidden_catalog_variants(db),
        **prompt_controls,
    )
    override = await catalog_override_selection(db, selection.variant, content_type_name=content_type_name)
    return override or selection
