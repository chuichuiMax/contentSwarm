from types import SimpleNamespace
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.material_library_service import MaterialCategoryDelete, MaterialCategoryUpdate
from yuxi.services.personal_materials import folder_categories, folder_counts, list_folder
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset, ContentCoverJob, ContentMaterialCategory, ContentMaterialLibraryItem, ImageDesignJob,
    ContentMaterialFolderSetting,
    ContentTask,
)


@pytest_asyncio.fixture
async def gallery_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("yuxi.services.material_library_service._audit", lambda *args, **kwargs: None)
    async with factory() as db:
        for owner in ("super", "alice", "bob"):
            for key, name in (("rough", "毛坯房图库"), ("uploads", "我的上传"),
                              ("generated", "AI生图图库"), ("works", "我的作品")):
                db.add(ContentMaterialCategory(owner_uid=owner, material_type="image", id=f"old-{key}",
                    name=name, visibility="private", industry_slug="decoration", is_system=False))
            db.add(ContentCoverAsset(id=f"asset-{owner}", owner_uid=owner, role="library_image",
                original_file_name="photo.jpg", content_type="image/jpeg", file_size=1,
                image_width=1, image_height=1, sha256=owner, bucket_name="image", object_name=owner))
            db.add(ContentMaterialLibraryItem(id=f"item-{owner}", owner_uid=owner, asset_id=f"asset-{owner}",
                material_type="image", display_name="photo", category="old-rough", category_owner_uid=owner))
        await db.commit()
        yield db
    await engine.dispose()


def user(uid="super", role="superadmin"):
    return SimpleNamespace(uid=uid, role=role, department_id=None)


@pytest.mark.asyncio
async def test_global_edit_keeps_historical_ids_and_private_items(gallery_db):
    from yuxi.services.personal_materials import update_personal_gallery
    await update_personal_gallery(gallery_db, user(), "rough",
        MaterialCategoryUpdate(name="施工实拍", description="全员说明", industry_slug="uncategorized"))
    for owner in ("alice", "bob"):
        folders = await folder_counts(gallery_db, user(owner, "user"))
        rough = next(f for f in folders if f["id"] == "rough")
        assert rough["name"] == "施工实拍"
        assert rough["description"] == "全员说明"
        assert rough["gallery_id"] == "old-rough"
        listing = await list_folder(gallery_db, user(owner, "user"), "rough", page=1, page_size=20)
        assert [i["id"] for i in listing["items"]] == [f"item-{owner}"]
        assert listing["items"][0]["category_name"] == "施工实拍"


@pytest.mark.asyncio
async def test_global_delete_migrates_each_account_without_restoring(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    await delete_personal_gallery(gallery_db, user(), user(), "rough",
        MaterialCategoryDelete(target_category_id="old-uploads"))
    for owner in ("super", "alice", "bob"):
        for _ in range(2):
            mapping = await folder_categories(gallery_db, user(owner, "user"))
            assert mapping["rough"] == []
        item = await gallery_db.get(ContentMaterialLibraryItem, f"item-{owner}")
        assert (item.owner_uid, item.category_owner_uid, item.category) == (owner, owner, "old-uploads")
        assert (await gallery_db.get(ContentCoverAsset, f"asset-{owner}")).deleted_at is None
        assert "rough" not in {f["id"] for f in await folder_counts(gallery_db, user(owner, "user"))}
    assert (await folder_categories(gallery_db, user("new-user", "user")))["rough"] == []


@pytest.mark.asyncio
async def test_regular_admin_cannot_change_global_gallery(gallery_db):
    from yuxi.services.personal_materials import update_personal_gallery, delete_personal_gallery
    for role in ("admin", "user"):
        with pytest.raises(HTTPException) as exc:
            await update_personal_gallery(gallery_db, user("alice", role), "rough", MaterialCategoryUpdate(name="越权"))
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            await delete_personal_gallery(gallery_db, user("alice", role), user("alice", role), "rough",
                MaterialCategoryDelete())
        assert exc.value.status_code == 403
    rows = list(await gallery_db.scalars(select(ContentMaterialCategory)))
    assert all(row.deleted_at is None for row in rows)


@pytest.mark.asyncio
async def test_deleted_generated_target_is_unavailable(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    from yuxi.image_design.save_targets import list_mp_save_targets, resolve_mp_save_target
    from yuxi.image_design.schemas import ImageDesignSaveTarget
    await delete_personal_gallery(gallery_db, user(), user(), "generated",
        MaterialCategoryDelete(target_category_id="old-uploads"))
    target = (await list_mp_save_targets(gallery_db, user("alice", "user")))["scopes"][0]
    assert target["folders"] == [] and target["can_write_root"] is False
    with pytest.raises(HTTPException) as exc:
        await resolve_mp_save_target(gallery_db, user("alice", "user"), ImageDesignSaveTarget(scope="private"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_works_delete_preserves_task_only_assets_and_existing_other_gallery(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    for asset_id in ("only-task", "already-filed"):
        gallery_db.add(ContentCoverAsset(id=asset_id, owner_uid="alice", role="output",
            original_file_name="work.jpg", content_type="image/jpeg", file_size=1,
            image_width=1, image_height=1, sha256=asset_id, bucket_name="content-covers", object_name=asset_id))
    gallery_db.add(ContentMaterialLibraryItem(id="filed", owner_uid="alice", asset_id="already-filed",
        material_type="image", display_name="work", category="old-generated", category_owner_uid="alice"))
    gallery_db.add(ContentCoverJob(id="work-job", owner_uid="alice", content_task_id="task-work",
        mode="generate", status="succeeded",
        idempotency_key="work-job", result_json={"asset_ids": ["only-task", "already-filed"]}))
    await gallery_db.commit()
    await delete_personal_gallery(gallery_db, user(), user(), "works",
        MaterialCategoryDelete(target_category_id="old-uploads"))
    material = await gallery_db.scalar(select(ContentMaterialLibraryItem).where(
        ContentMaterialLibraryItem.asset_id == "only-task"))
    assert (material.category, material.owner_uid, material.category_owner_uid) == ("old-uploads", "alice", "alice")
    assert (await gallery_db.get(ContentMaterialLibraryItem, "filed")).category == "old-generated"
    job = await gallery_db.get(ContentCoverJob, "work-job")
    assert job.status == "succeeded" and job.result_json["asset_ids"] == ["only-task", "already-filed"]
    assert (await gallery_db.get(ContentCoverAsset, "only-task")).deleted_at is None


@pytest.mark.asyncio
async def test_delete_generated_with_pending_output_is_blocked(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    gallery_db.add(ImageDesignJob(id="pending", owner_uid="alice", workflow="direct", status="running",
        idempotency_key="pending", request_json={"requested_save_target": {
            "scope": "private", "gallery_id": "old-generated"}}))
    await gallery_db.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_personal_gallery(gallery_db, user(), user(), "generated", MaterialCategoryDelete())
    assert exc.value.status_code == 409
    assert (await gallery_db.get(ContentMaterialCategory, ("alice", "image", "old-generated"))).deleted_at is None


@pytest.mark.asyncio
async def test_delete_generated_moves_historical_root_saves_without_a_gallery_entity(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    await gallery_db.execute(delete(ContentMaterialCategory).where(
        ContentMaterialCategory.owner_uid == "alice", ContentMaterialCategory.id == "old-generated"))
    gallery_db.add(ContentMaterialCategory(owner_uid="alice", material_type="image", id="private-root",
        name="我的素材（根目录）", visibility="private", industry_slug="uncategorized", is_system=True))
    asset = await gallery_db.get(ContentMaterialLibraryItem, "item-alice")
    asset.category = "private-root"
    asset.metadata_json = {"source": "image_design"}
    await gallery_db.commit()
    await delete_personal_gallery(gallery_db, user(), user(), "generated",
        MaterialCategoryDelete(target_category_id="old-uploads"))
    assert (asset.category, asset.category_owner_uid, asset.owner_uid) == ("old-uploads", "alice", "alice")
    assert (await folder_categories(gallery_db, user("alice", "user")))["generated"] == []


@pytest.mark.asyncio
async def test_failed_global_migration_rolls_back_earlier_accounts(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    # alice migrates before bob, whose missing asset must abort the whole operation.
    bob_asset = await gallery_db.get(ContentCoverAsset, "asset-bob")
    bob_asset.deleted_at = datetime.now()
    await gallery_db.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_personal_gallery(gallery_db, user(), user(), "rough",
            MaterialCategoryDelete(target_category_id="old-uploads"))
    assert exc.value.status_code == 409
    await gallery_db.rollback()
    alice = await gallery_db.get(ContentMaterialLibraryItem, "item-alice")
    assert alice.category == "old-rough"
    assert (await gallery_db.get(ContentMaterialCategory, ("alice", "image", "old-rough"))).deleted_at is None
    assert await gallery_db.get(ContentMaterialFolderSetting, "rough") is None


@pytest.mark.asyncio
async def test_deleted_generated_gallery_rejects_pc_cover_before_enqueue(gallery_db):
    from yuxi.services.content_cover_service import _create_job
    from yuxi.services.personal_materials import delete_personal_gallery
    gallery_db.add(ContentTask(id="pc-task", name="PC内容", industry_template_version_id="industry",
        workflow_version_id="workflow", rule_version_id="rule", created_by="alice", updated_by="alice"))
    await gallery_db.commit()
    await delete_personal_gallery(gallery_db, user(), user(), "generated", MaterialCategoryDelete())
    with pytest.raises(HTTPException) as exc:
        await _create_job(gallery_db, user("alice", "user"), mode="generate", content_task_id="pc-task",
            artifact_id=None, idempotency_key="deleted-target", request={})
    assert exc.value.status_code == 409
    assert await gallery_db.scalar(select(ContentCoverJob.id)) is None


@pytest.mark.asyncio
async def test_pending_mp_cover_does_not_block_unrelated_generated_gallery(gallery_db):
    from yuxi.services.personal_materials import delete_personal_gallery
    gallery_db.add(ContentTask(id="mp-task", name="小程序内容", industry_template_version_id="industry",
        workflow_version_id="workflow", rule_version_id="rule", created_by="alice", updated_by="alice",
        brief_json={"form_values": {"mp_content_code": "case"}}))
    gallery_db.add(ContentCoverJob(id="mp-cover", owner_uid="alice", content_task_id="mp-task", mode="generate",
        status="running", idempotency_key="mp-cover"))
    await gallery_db.commit()
    await delete_personal_gallery(gallery_db, user(), user(), "generated", MaterialCategoryDelete())
    assert (await gallery_db.get(ContentCoverJob, "mp-cover")).status == "running"
