from __future__ import annotations

import asyncio
import hashlib
import io
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_material_migration_apply_verify_and_rollback_preserves_old_object(tmp_path):
    from yuxi.storage.postgres.manager import pg_manager

    pg_manager.initialize()
    await pg_manager.create_business_tables()
    await pg_manager.close()

    uid = f"pytest_migration_{uuid.uuid4().hex[:10]}"
    asset_id = f"cca_{uuid.uuid4().hex}"
    old_bucket = "content-covers"
    old_object = f"pytest-material-migration/{asset_id}.png"
    target_object = f"material-library/{uid}/images/{asset_id}/image.png"
    output = io.BytesIO()
    Image.new("RGBA", (40, 30), "#25A36F").save(output, format="PNG")
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    storage = get_minio_client()
    await storage.aupload_file(old_bucket, old_object, data, "image/png")

    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(
            ContentCoverAsset(
                id=asset_id,
                owner_uid=uid,
                tenant_id=None,
                content_task_id=None,
                role="source",
                original_file_name="migration.png",
                content_type="image/png",
                file_size=len(data),
                image_width=40,
                image_height=30,
                sha256=digest,
                bucket_name=old_bucket,
                object_name=old_object,
                metadata_json={},
            )
        )
        await db.commit()

    manifest = tmp_path / "manifest.jsonl"
    base = [sys.executable, "scripts/migrate_material_library_to_image.py", "--owner-uid", uid]
    try:
        dry_run = subprocess.run(base, cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=30)
        assert dry_run.returncode == 0, dry_run.stderr
        assert f"PLAN {asset_id}:" in dry_run.stdout
        assert "apply=False planned=1 migrated=0" in dry_run.stdout
        assert not manifest.exists()
        async with sessions() as db:
            asset = await db.get(ContentCoverAsset, asset_id)
            assert (asset.bucket_name, asset.object_name) == (old_bucket, old_object)
        image_bucket_exists = await asyncio.to_thread(storage.client.bucket_exists, "image")
        assert not image_bucket_exists or await storage.astat_file("image", target_object) is None

        applied = subprocess.run(
            [*base, "--apply", "--manifest-path", str(manifest)],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert applied.returncode == 0, applied.stderr
        verified = subprocess.run([*base, "--verify"], cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=30)
        assert verified.returncode == 0, verified.stderr

        async with sessions() as db:
            asset = await db.get(ContentCoverAsset, asset_id)
            assert (asset.bucket_name, asset.object_name) == ("image", target_object)
            assert await db.scalar(
                ContentMaterialLibraryItem.__table__.select()
                .with_only_columns(ContentMaterialLibraryItem.id)
                .where(ContentMaterialLibraryItem.asset_id == asset_id)
            )
        assert await storage.adownload_file(old_bucket, old_object) == data
        assert await storage.adownload_file("image", target_object) == data

        rolled_back = subprocess.run(
            [
                sys.executable,
                "scripts/migrate_material_library_to_image.py",
                "--rollback",
                "--manifest-path",
                str(manifest),
            ],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rolled_back.returncode == 0, rolled_back.stderr
        async with sessions() as db:
            asset = await db.get(ContentCoverAsset, asset_id)
            assert (asset.bucket_name, asset.object_name) == (old_bucket, old_object)
        assert await storage.adownload_file("image", target_object) == data
    finally:
        async with sessions() as db:
            await db.execute(delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.asset_id == asset_id))
            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid == uid))
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.id == asset_id))
            await db.commit()
        await engine.dispose()
        await storage.adelete_file(old_bucket, old_object)
        await storage.adelete_file("image", target_object)
