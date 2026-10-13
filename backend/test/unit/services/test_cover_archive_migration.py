from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.personal_materials import folder_counts, list_folder
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset, ContentCoverJob, ContentCoverPosterTemplate, ContentMaterialCategory,
    ContentMaterialFolderSetting, ContentMaterialLibraryItem, ContentMaterialUsage, ContentTask,
)

SPEC = importlib.util.spec_from_file_location(
    "migrate_content_cover_archives", Path(__file__).resolve().parents[3] / "scripts/migrate_content_cover_archives.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest_asyncio.fixture
async def archive_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [model.__table__ for model in (
        Department, User, ContentCoverAsset, ContentCoverJob, ContentTask, ContentMaterialCategory,
        ContentMaterialLibraryItem, ContentMaterialFolderSetting, ContentMaterialUsage, ContentCoverPosterTemplate,
    )]
    async with engine.begin() as conn:
        await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for owner in ("alice", "bob"):
            db.add(User(uid=owner, username=owner, password_hash="unused", role="user"))
            db.add(ContentMaterialCategory(owner_uid=owner, id="mp-generated-private", material_type="image",
                                          name="AI生图图库", visibility="private"))
        db.add(ContentMaterialCategory(owner_uid="alice", id="shared", material_type="image",
                                      name="企业生图图库", visibility="enterprise"))
        cases = [
            ("pc", "alice", {}, "mp-generated-private", {}, True),
            ("mp-hidden", "alice", {"mp_content_code": "MP1"}, "mp-generated-private", {}, True),
            ("design", "alice", {}, "mp-generated-private", {"domain": "image_design"}, True),
            ("pending", "alice", {}, "mp-generated-private", {}, False),
            ("shared", "alice", {}, "shared", {}, True),
            ("bob-cover", "bob", {}, "mp-generated-private", {}, True),
        ]
        for asset_id, owner, values, category, metadata, completed in cases:
            db.add(ContentTask(id=f"task-{asset_id}", name=asset_id, created_by=owner, updated_by=owner,
                               industry_template_version_id="industry", workflow_version_id="workflow",
                               rule_version_id="rule", brief_json={"form_values": values}))
            db.add(ContentCoverAsset(id=asset_id, owner_uid=owner, role="output", content_task_id=f"task-{asset_id}",
                                     original_file_name=f"{asset_id}.png", content_type="image/png", file_size=10,
                                     image_width=32, image_height=24, sha256=asset_id, bucket_name="covers",
                                     object_name=f"object-{asset_id}", metadata_json=metadata,
                                     hidden_from_works_at=datetime(2026, 10, 1) if asset_id == "mp-hidden" else None))
            db.add(ContentMaterialLibraryItem(id=f"item-{asset_id}", owner_uid=owner, asset_id=asset_id,
                                              material_type="image", display_name=asset_id, category=category,
                                              category_owner_uid=owner, metadata_json={"source": "content_production"}))
            db.add(ContentCoverJob(id=f"job-{asset_id}", owner_uid=owner, content_task_id=f"task-{asset_id}",
                                   mode="compose", status="succeeded" if completed else "running",
                                   idempotency_key=asset_id, result_json={"asset_ids": [asset_id]}))
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_archive_preview_is_read_only_and_apply_preserves_assets(archive_db, tmp_path):
    db = archive_db
    preview = await MODULE.migrate_archives(db, ["alice"])
    assert {row["asset_id"] for row in preview["changes"]} == {"pc", "mp-hidden"}
    assert preview["skipped"] == [{"item_id": "item-pending", "reason": "缺少同账号成功封面任务的关联证据"}]
    assert (await db.get(ContentMaterialLibraryItem, "item-pc")).category == "mp-generated-private"
    assert await db.get(ContentMaterialCategory, ("alice", "image", "mp-works-private")) is None
    manifest = tmp_path / "before.json"
    applied = await MODULE.migrate_archives(db, ["alice"], apply=True, manifest_path=manifest)
    assert applied["count"] == 2
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert all(row["category"] == "mp-generated-private" for row in saved["changes"])
    for asset_id, channel in (("pc", "pc"), ("mp-hidden", "mp")):
        item = await db.get(ContentMaterialLibraryItem, f"item-{asset_id}")
        asset = await db.get(ContentCoverAsset, asset_id)
        assert (item.category, item.category_owner_uid) == ("mp-works-private", "alice")
        assert item.metadata_json["source_channel"] == channel
        assert item.metadata_json["retain_asset_on_delete"] is True
        assert (asset.id, asset.sha256, asset.bucket_name, asset.object_name) == (
            asset_id, asset_id, "covers", f"object-{asset_id}",
        )
        assert asset.deleted_at is None
    assert (await db.get(ContentCoverAsset, "mp-hidden")).hidden_from_works_at == datetime(2026, 10, 1)
    for asset_id in ("design", "pending", "bob-cover"):
        assert (await db.get(ContentMaterialLibraryItem, f"item-{asset_id}")).category == "mp-generated-private"
    assert (await db.get(ContentMaterialLibraryItem, "item-shared")).category == "shared"
    assert (await MODULE.migrate_archives(db, ["alice"], apply=True, manifest_path=manifest))["count"] == 0


@pytest.mark.asyncio
async def test_migration_requires_scope_and_never_overwrites_manifest(archive_db, tmp_path):
    with pytest.raises(ValueError, match="指定账号"):
        await MODULE.migrate_archives(archive_db, ["missing"])
    with pytest.raises(ValueError, match="manifest-path"):
        await MODULE.migrate_archives(archive_db, ["alice"], apply=True)
    manifest = tmp_path / "before.json"
    manifest.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        await MODULE.migrate_archives(archive_db, ["alice"], apply=True, manifest_path=manifest)
    assert manifest.read_text(encoding="utf-8") == "original"
    assert (await archive_db.get(ContentMaterialLibraryItem, "item-pc")).category == "mp-generated-private"


@pytest.mark.asyncio
async def test_migration_rolls_back_records_and_keeps_manifest(archive_db, tmp_path):
    manifest = tmp_path / "before.json"
    await MODULE.migrate_archives(archive_db, ["alice"], apply=True, manifest_path=manifest)
    await archive_db.rollback()
    assert manifest.exists()
    assert (await archive_db.get(ContentMaterialLibraryItem, "item-pc")).category == "mp-generated-private"
    assert (await archive_db.get(ContentCoverAsset, "pc")).metadata_json == {}
    assert await archive_db.get(ContentMaterialCategory, ("alice", "image", "mp-works-private")) is None


@pytest.mark.asyncio
async def test_migration_rejects_deleted_works_without_changing_records(archive_db, tmp_path):
    archive_db.add(ContentMaterialFolderSetting(folder_key="works", name="我的作品", config_version=1,
                                                deleted_at=datetime(2026, 10, 12)))
    await archive_db.commit()
    manifest = tmp_path / "before.json"
    with pytest.raises(HTTPException) as error:
        await MODULE.migrate_archives(archive_db, ["alice"], apply=True, manifest_path=manifest)
    assert error.value.status_code == 409
    assert not manifest.exists()
    assert (await archive_db.get(ContentMaterialLibraryItem, "item-pc")).category == "mp-generated-private"


@pytest.mark.asyncio
async def test_cover_is_only_in_works_and_counts_match_before_and_after_migration(archive_db):
    db = archive_db
    owner = await db.get(User, 1)
    for migrated in (False, True):
        if migrated:
            # Application above is tested separately; exercise the resulting existing attachment.
            item = await db.get(ContentMaterialLibraryItem, "item-pc")
            item.category = "mp-works-private"
            await db.flush()
        generated = await list_folder(db, owner, "generated", page=1, page_size=100)
        assert {row["asset_id"] for row in generated["items"]} == {"design"}
        works = await list_folder(db, owner, "works", page=1, page_size=100)
        assert [row["asset_id"] for row in works["items"]].count("pc") == 1
        assert "mp-hidden" not in {row["asset_id"] for row in works["items"]}
        counts = {row["id"]: row for row in await folder_counts(db, owner, client="pc")}
        assert counts["generated"]["count"] == generated["total"] == 1
        assert counts["works"]["count"] == works["total"]
        assert counts["generated"]["cover_file_url"].endswith("/item-design/file")
