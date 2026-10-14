"""Import OSS ``image/material-library/<year>/`` into enterprise case secondary galleries.

Each top-level folder becomes a secondary gallery under the enterprise 案例图库.
Downloads originals from OSS, normalizes to WebP, uploads thumbnail, creates
``content_cover_assets`` + ``content_material_library_items``, and logs material IDs.

Default prefix: ``image/material-library/2026/``. Use ``--prefix`` for other years.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import gc
import hashlib
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path

import alibabacloud_oss_v2 as oss
from fastapi import HTTPException
from sqlalchemy import select, text

from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services.material_library_service import (
    MATERIAL_LIBRARY_BUCKET,
    MaterialCategoryCreate,
    _normalize_image,
    create_library_item_for_asset,
    create_material_category,
    watermark_upload_bytes,
)
from yuxi.services.material_upload_queue import (
    INGEST_COMPLETED,
    SHOW_AFTER_ORIGINAL,
    encode_material_thumbnail,
    material_thumb_object_name,
)
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

CASE_ROOT_ID = "mlc_a228707511984df3b34c9b7beafd66af"
ACTOR_UID = "mp_0cd4e4d8b07f4d26b"
DEFAULT_PREFIX = "image/material-library/2026/"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}
STYLES = (
    "新装饰主义",
    "复合写意",
    "写意木构",
    "江南印象",
    "东方古雅",
    "轻欧简美",
    "欧美香颂",
    "欧式田园",
    "异域风情",
    "北欧之光",
    "意境东方",
    "雅致现代",
    "工业再造",
    "优雅缤纷",
    "极简侘寂",
    "仿生未来",
    "复古风潮",
    "艺术室界",
    "雅致灰调",
    "意式轻奢",
    "意式极简",
    "中古风潮",
    "宋氏美学",
    "现代之源",
    "复古写意",
)
TYPOS = {"极简诧寂": "极简侘寂", "艺术境界": "艺术室界"}
_CASE_FOLDER_NAME = re.compile(
    r"^(?P<building>.+?)[\s\-·・｜|_]+(?P<area>\d+(?:\.\d+)?)(?:\s*(?:㎡|m2|m²))?$",
    re.IGNORECASE,
)

OSS_PREFIX = DEFAULT_PREFIX


def normalize_prefix(value: str) -> str:
    prefix = value.strip()
    if not prefix:
        raise ValueError("prefix 不能为空")
    if not prefix.endswith("/"):
        prefix += "/"
    return prefix


def release_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        return


def resolve_style(folder: str) -> str | None:
    hits = [style for style in sorted(STYLES, key=len, reverse=True) if style in folder]
    chosen: list[str] = []
    for style in hits:
        if any(style != longer and style in longer for longer in chosen):
            continue
        chosen.append(style)
    if len(chosen) == 1:
        return chosen[0]
    if len(chosen) > 1:
        return None
    for typo, official in TYPOS.items():
        if typo in folder:
            return official
    return None


def list_sources() -> tuple[dict[str, list[str]], list[str]]:
    storage = get_minio_client()
    client = storage._oss
    paginator = client.client.list_objects_v2_paginator()
    grouped: dict[str, list[str]] = defaultdict(list)
    skipped: list[str] = []
    for page in paginator.iter_page(oss.ListObjectsV2Request(bucket=client.bucket, prefix=OSS_PREFIX)):
        for obj in page.contents or []:
            key = obj.key or ""
            relative = key[len(OSS_PREFIX) :] if key.startswith(OSS_PREFIX) else key
            if not relative or relative.endswith("/"):
                continue
            folder = relative.split("/", 1)[0] if "/" in relative else ""
            file_name = Path(relative).name
            suffix = Path(file_name).suffix.lower()
            if (
                not folder
                or file_name.startswith(".")
                or suffix not in IMAGE_SUFFIXES
                or ".thumb." in file_name
                or ".share-v1." in file_name
            ):
                skipped.append(relative)
                continue
            grouped[folder].append(key)
    for keys in grouped.values():
        keys.sort()
    return grouped, skipped


async def load_user(db, uid: str) -> User | None:
    return (await db.execute(select(User).where(User.uid == uid))).scalar_one_or_none()


async def existing_galleries() -> dict[str, str]:
    async with pg_manager.get_async_session_context() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT name, id
                    FROM content_material_categories
                    WHERE owner_uid = 'tiechui'
                      AND material_type = 'image'
                      AND parent_id = :parent
                      AND deleted_at IS NULL
                    """
                ),
                {"parent": CASE_ROOT_ID},
            )
        ).mappings()
        return {row["name"]: row["id"] for row in rows}


async def ensure_gallery(folder: str, style: str, galleries: dict[str, str]) -> str:
    if folder in galleries:
        print(f"GALLERY_EXISTS\t{galleries[folder]}\t{style}\t{folder}", flush=True)
        return galleries[folder]
    match = _CASE_FOLDER_NAME.fullmatch(folder.strip())
    building = match.group("building").strip() if match else ""
    area = match.group("area") if match else None
    async with pg_manager.get_async_session_context() as db:
        actor = await load_user(db, ACTOR_UID)
        if actor is None:
            raise RuntimeError(f"actor {ACTOR_UID} not found")
        payload = MaterialCategoryCreate(
            visibility="enterprise",
            material_type="image",
            parent_id=CASE_ROOT_ID,
            name=folder,
            description="",
            design_style=style,
            building_name=building or None,
            area=area,
        )
        try:
            created = await create_material_category(db, actor, payload)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = ((detail.get("error") or {}) if isinstance(detail, dict) else {}).get("code")
            if code != "MATERIAL_CATEGORY_NAME_DUPLICATE":
                raise
            found = await existing_galleries()
            galleries.update(found)
            if folder not in galleries:
                raise
            print(f"GALLERY_EXISTS\t{galleries[folder]}\t{style}\t{folder}", flush=True)
            return galleries[folder]
        category_id = created["category"]["id"]
        galleries[folder] = category_id
        print(f"GALLERY_CREATED\t{category_id}\t{style}\t{folder}", flush=True)
        return category_id


async def existing_files(category_id: str) -> tuple[set[str], set[str]]:
    async with pg_manager.get_async_session_context() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT a.original_file_name AS file_name,
                           a.metadata_json->>'source_object' AS source_object
                    FROM content_material_library_items i
                    JOIN content_cover_assets a ON a.id = i.asset_id
                    WHERE i.category = :category
                      AND i.category_owner_uid = 'tiechui'
                      AND i.deleted_at IS NULL
                      AND a.deleted_at IS NULL
                    """
                ),
                {"category": category_id},
            )
        ).mappings()
        names: set[str] = set()
        sources: set[str] = set()
        for row in rows:
            if row["file_name"]:
                names.add(row["file_name"])
            if row["source_object"]:
                sources.add(row["source_object"])
        return names, sources


def stored_file_name(folder: str, physical_key: str, names_in_folder: list[str]) -> str:
    relative = physical_key[len(OSS_PREFIX) :]
    base = Path(relative).name
    if names_in_folder.count(base) == 1:
        return base
    return relative


async def import_one(category_id: str, physical_key: str, file_name: str, style: str) -> str:
    source_name = physical_key[len(f"{MATERIAL_LIBRARY_BUCKET}/") :]
    storage = get_minio_client()
    raw = await storage.adownload_file(MATERIAL_LIBRARY_BUCKET, source_name)
    try:
        normalized, width, height, content_type = _normalize_image(raw)
        del raw
        stored = watermark_upload_bytes(
            normalized,
            category_id=category_id,
            owner_uid="tiechui",
            parent_id=CASE_ROOT_ID,
        )
        del normalized
        thumbnail = encode_material_thumbnail(stored)
        asset_id = f"cca_{uuid.uuid4().hex}"
        object_name = f"material-library/{ACTOR_UID}/images/{asset_id}/image.webp"
        thumb_name = material_thumb_object_name(object_name)
        await storage.aupload_file(MATERIAL_LIBRARY_BUCKET, object_name, stored, content_type=content_type)
        await storage.aupload_file(MATERIAL_LIBRARY_BUCKET, thumb_name, thumbnail, content_type="image/webp")
        original_size = await storage.astat_file(MATERIAL_LIBRARY_BUCKET, object_name)
        thumb_size = await storage.astat_file(MATERIAL_LIBRARY_BUCKET, thumb_name)
        if not original_size or not thumb_size:
            raise RuntimeError(f"stored object missing after upload: {asset_id}")
        file_size = len(stored)
        digest = hashlib.sha256(stored).hexdigest()
        suffix = Path(file_name).suffix.lower()
        original_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
        del stored
        del thumbnail
        async with pg_manager.get_async_session_context() as db:
            actor = await load_user(db, ACTOR_UID)
            if actor is None:
                raise RuntimeError(f"actor {ACTOR_UID} not found")
            asset = await ContentCoverRepository(db).create_asset(
                id=asset_id,
                owner_uid=actor.uid,
                tenant_id=str(actor.department_id) if actor.department_id is not None else None,
                content_task_id=None,
                role="library_image",
                original_file_name=file_name,
                content_type=content_type,
                file_size=file_size,
                image_width=width,
                image_height=height,
                sha256=digest,
                bucket_name=MATERIAL_LIBRARY_BUCKET,
                object_name=object_name,
                metadata_json={
                    "original_content_type": original_type,
                    "source_channel": "oss",
                    "source_object": physical_key,
                    "ingest_status": INGEST_COMPLETED,
                    SHOW_AFTER_ORIGINAL: True,
                },
            )
            item = await create_library_item_for_asset(
                db,
                asset=asset,
                material_type="image",
                name=Path(file_name).stem[:255],
                category=category_id,
                category_owner_uid="tiechui",
                metadata={"source_channel": "oss", "design_style": style},
            )
            item_id = item.id
        return f"OK\t{file_name}\t{item_id}\t{asset_id}\t{width}x{height}\t{file_size}\tthumb={thumb_size}"
    finally:
        release_memory()


async def main() -> None:
    grouped, skipped = list_sources()
    print(f"PREFIX\t{OSS_PREFIX}", flush=True)
    print(
        f"SOURCE\tfolders={len(grouped)}\timages={sum(len(v) for v in grouped.values())}\tskipped_files={len(skipped)}",
        flush=True,
    )
    for relative in skipped[:40]:
        print(f"SKIP_FILE\t{relative}", flush=True)
    galleries = await existing_galleries()
    created = 0
    skipped_existing = 0
    failed = 0
    for index, folder in enumerate(sorted(grouped), start=1):
        style = resolve_style(folder)
        if not style:
            print(f"SKIP_STYLE\t{folder}\t{len(grouped[folder])}", flush=True)
            failed += len(grouped[folder])
            continue
        try:
            category_id = await ensure_gallery(folder, style, galleries)
        except Exception as exc:
            print(f"FAIL_GALLERY\t{folder}\t{type(exc).__name__}\t{exc}", flush=True)
            failed += len(grouped[folder])
            continue
        names, sources = await existing_files(category_id)
        basenames = [Path(key).name for key in grouped[folder]]
        print(f"FOLDER\t{index}/{len(grouped)}\t{folder}\t{category_id}\t{len(grouped[folder])}", flush=True)
        for key in grouped[folder]:
            file_name = stored_file_name(folder, key, basenames)
            if key in sources:
                print(f"SKIP\t{folder}\t{file_name}\t{key}", flush=True)
                skipped_existing += 1
                continue
            try:
                line = await import_one(category_id, key, file_name, style)
            except Exception as exc:
                print(f"FAIL\t{folder}\t{file_name}\t{type(exc).__name__}\t{exc}", flush=True)
                failed += 1
                continue
            names.add(file_name)
            sources.add(key)
            created += 1
            print(f"{line}\t{folder}\t{category_id}", flush=True)
    print(f"DONE\tcreated={created}\tskipped={skipped_existing}\tfailed={failed}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        default=DEFAULT_PREFIX,
        help=f"OSS object prefix (default: {DEFAULT_PREFIX})",
    )
    args = parser.parse_args()
    OSS_PREFIX = normalize_prefix(args.prefix)
    asyncio.run(main())
