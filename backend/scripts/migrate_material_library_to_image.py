from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select

APP_ROOT = Path(__file__).resolve().parents[1]
for import_path in (APP_ROOT, APP_ROOT / "package"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

ELIGIBLE_ROLES = {
    "source": "image",
    "template": "cover_template",
    "mask": "cover_template",
    "poster_template": "cover_template",
}
VISIBLE_LIBRARY_ROLES = {"source", "template", "poster_template"}


def target_object_name(owner_uid: str, asset_id: str, material_type: str) -> str:
    folder = "images" if material_type == "image" else "cover-templates"
    return f"material-library/{owner_uid}/{folder}/{asset_id}/image.png"


def append_manifest(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"迁移清单不存在: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def migrate(*, apply: bool, owner_uid: str | None, manifest_path: Path) -> int:
    from yuxi.services.material_library_service import MATERIAL_LIBRARY_BUCKET, create_library_item_for_asset
    from yuxi.storage.minio import get_minio_client
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_content import ContentCoverAsset

    pg_manager.initialize()
    if apply:
        await pg_manager.create_business_tables()
    client = get_minio_client()
    planned = migrated = skipped = failed = 0
    async with pg_manager.get_async_session_context() as db:
        filters = [ContentCoverAsset.role.in_(ELIGIBLE_ROLES), ContentCoverAsset.deleted_at.is_(None)]
        if owner_uid:
            filters.append(ContentCoverAsset.owner_uid == owner_uid)
        assets = list(
            (await db.execute(select(ContentCoverAsset).where(*filters).order_by(ContentCoverAsset.id))).scalars()
        )
        for asset in assets:
            material_type = ELIGIBLE_ROLES[asset.role]
            target = target_object_name(asset.owner_uid, asset.id, material_type)
            if asset.bucket_name == MATERIAL_LIBRARY_BUCKET and asset.object_name == target:
                if apply and asset.role in VISIBLE_LIBRARY_ROLES:
                    await create_library_item_for_asset(
                        db,
                        asset=asset,
                        material_type=material_type,
                        name=Path(asset.original_file_name).stem,
                        metadata={"migrated_role": asset.role},
                    )
                    await db.commit()
                skipped += 1
                continue
            print(f"PLAN {asset.id}: {asset.bucket_name}/{asset.object_name} -> {MATERIAL_LIBRARY_BUCKET}/{target}")
            planned += 1
            if not apply:
                continue
            old_bucket, old_object = asset.bucket_name, asset.object_name
            try:
                data = await client.adownload_file(old_bucket, old_object)
                if hashlib.sha256(data).hexdigest() != asset.sha256:
                    raise ValueError("源对象 SHA-256 与数据库不一致")
                uploaded = await client.aupload_file(
                    bucket_name=MATERIAL_LIBRARY_BUCKET,
                    object_name=target,
                    data=data,
                    content_type=asset.content_type,
                )
                copied = await client.adownload_file(uploaded.bucket_name, uploaded.object_name)
                if len(copied) != asset.file_size or hashlib.sha256(copied).hexdigest() != asset.sha256:
                    raise ValueError("目标对象完整性校验失败")
                asset.bucket_name = uploaded.bucket_name
                asset.object_name = uploaded.object_name
                if asset.role in VISIBLE_LIBRARY_ROLES:
                    await create_library_item_for_asset(
                        db,
                        asset=asset,
                        material_type=material_type,
                        name=Path(asset.original_file_name).stem,
                        metadata={"migrated_role": asset.role},
                    )
                append_manifest(
                    manifest_path,
                    {
                        "asset_id": asset.id,
                        "old_bucket": old_bucket,
                        "old_object": old_object,
                        "new_bucket": uploaded.bucket_name,
                        "new_object": uploaded.object_name,
                        "sha256": asset.sha256,
                    },
                )
                await db.commit()
                migrated += 1
            except Exception as exc:
                await db.rollback()
                failed += 1
                print(f"FAILED {asset.id}: {exc}", file=sys.stderr)
                break
    print(f"SUMMARY apply={apply} planned={planned} migrated={migrated} skipped={skipped} failed={failed}")
    await pg_manager.close()
    return 1 if failed else 0


async def rollback(manifest_path: Path) -> int:
    from yuxi.storage.minio import get_minio_client
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_content import ContentCoverAsset

    records = read_manifest(manifest_path)
    pg_manager.initialize()
    client = get_minio_client()
    async with pg_manager.get_async_session_context() as db:
        for record in reversed(records):
            asset = await db.get(ContentCoverAsset, record["asset_id"])
            if asset is None:
                raise RuntimeError(f"资产不存在: {record['asset_id']}")
            if (asset.bucket_name, asset.object_name) == (record["old_bucket"], record["old_object"]):
                continue
            if (asset.bucket_name, asset.object_name) != (record["new_bucket"], record["new_object"]):
                raise RuntimeError(f"资产位置已发生额外变化: {asset.id}")
            old_data = await client.adownload_file(record["old_bucket"], record["old_object"])
            if hashlib.sha256(old_data).hexdigest() != record["sha256"]:
                raise RuntimeError(f"Source object integrity check failed: {asset.id}")
            asset.bucket_name = record["old_bucket"]
            asset.object_name = record["old_object"]
        await db.commit()
    await pg_manager.close()
    print(f"ROLLBACK restored={len(records)}; image 桶对象按安全策略保留")
    return 0


async def verify(owner_uid: str | None) -> int:
    from yuxi.repositories.material_library_repository import MaterialLibraryRepository
    from yuxi.services.material_library_service import MATERIAL_LIBRARY_BUCKET
    from yuxi.storage.minio import get_minio_client
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_content import ContentCoverAsset

    pg_manager.initialize()
    client = get_minio_client()
    checked = failed = 0
    async with pg_manager.get_async_session_context() as db:
        filters = [ContentCoverAsset.role.in_(ELIGIBLE_ROLES), ContentCoverAsset.deleted_at.is_(None)]
        if owner_uid:
            filters.append(ContentCoverAsset.owner_uid == owner_uid)
        assets = list((await db.execute(select(ContentCoverAsset).where(*filters))).scalars())
        repo = MaterialLibraryRepository(db)
        for asset in assets:
            checked += 1
            expected = target_object_name(asset.owner_uid, asset.id, ELIGIBLE_ROLES[asset.role])
            try:
                if (asset.bucket_name, asset.object_name) != (MATERIAL_LIBRARY_BUCKET, expected):
                    raise ValueError("数据库对象位置不符合素材库规则")
                data = await client.adownload_file(asset.bucket_name, asset.object_name)
                if len(data) != asset.file_size or hashlib.sha256(data).hexdigest() != asset.sha256:
                    raise ValueError("目标对象大小或 SHA-256 不一致")
                if asset.role in VISIBLE_LIBRARY_ROLES and await repo.get_item_by_asset(asset.id) is None:
                    raise ValueError("缺少素材库目录项")
            except Exception as exc:
                failed += 1
                print(f"FAILED {asset.id}: {exc}", file=sys.stderr)
    await pg_manager.close()
    print(f"VERIFY checked={checked} failed={failed}")
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将可复用内容素材迁移到私有 image 桶")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="执行迁移；默认仅 dry-run")
    mode.add_argument("--rollback", action="store_true", help="按 manifest 恢复数据库旧引用")
    mode.add_argument("--verify", action="store_true", help="校验数据库、目录项和 image 桶对象")
    parser.add_argument("--owner-uid")
    parser.add_argument("--manifest-path", type=Path, default=Path("saves/migrations/material-library-image.jsonl"))
    return parser.parse_args()


def main() -> int:
    load_dotenv(APP_ROOT.parent / ".env", override=False)
    args = parse_args()
    if args.rollback:
        return asyncio.run(rollback(args.manifest_path))
    if args.verify:
        return asyncio.run(verify(args.owner_uid))
    return asyncio.run(migrate(apply=args.apply, owner_uid=args.owner_uid, manifest_path=args.manifest_path))


if __name__ == "__main__":
    raise SystemExit(main())
