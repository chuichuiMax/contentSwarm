from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.mp_service import list_mp_works
from yuxi.services.personal_materials import _private_category_name, folder_counts, list_folder
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverJob,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
)


def test_private_folder_uses_distinct_storage_name_when_owner_already_has_shared_gallery():
    shared = ContentMaterialCategory(
        owner_uid="alice", id="existing", material_type="image", visibility="enterprise", name="毛坯房图库"
    )
    assert _private_category_name("rough", [shared], "alice") == "毛坯房图库（个人）"


@pytest.mark.asyncio
async def test_fixed_folders_share_pc_sources_but_keep_mini_uploads_private():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    enterprise_owner = "system:material-library"
    categories = [
        ContentMaterialCategory(
            owner_uid=enterprise_owner,
            id="rough-shared",
            material_type="image",
            visibility="enterprise",
            name="毛坯房图库",
            image_design_role="rough",
        ),
        ContentMaterialCategory(
            owner_uid=enterprise_owner,
            id="generated-shared",
            material_type="image",
            visibility="enterprise",
            name="生图图库",
        ),
        ContentMaterialCategory(
            owner_uid=enterprise_owner,
            id="mp-uploads-shared",
            material_type="image",
            visibility="enterprise",
            name="我的上传",
        ),
    ]
    for uid in ("alice", "bob", "charlie"):
        tenant_id = "tenant-2" if uid == "charlie" else "tenant-1"
        categories.extend(
            [
                ContentMaterialCategory(
                    owner_uid=uid,
                    id="mp-rough-private",
                    tenant_id=tenant_id,
                    material_type="image",
                    visibility="private",
                    name="毛坯房图库",
                ),
                ContentMaterialCategory(
                    owner_uid=uid,
                    id="mp-uploads-private",
                    tenant_id=tenant_id,
                    material_type="image",
                    visibility="private",
                    name="我的上传",
                ),
                ContentMaterialCategory(
                    owner_uid=uid,
                    id="private-root",
                    tenant_id=tenant_id,
                    material_type="image",
                    visibility="private",
                    name="我的素材（根目录）",
                    is_system=True,
                ),
            ]
        )
    photos = [
        ("alice-rough", "alice", "mp-rough-private", "alice", "mp", "rough", None),
        ("alice-upload", "alice", "mp-uploads-private", "alice", "mp", "uploads", None),
        ("bob-upload", "bob", "mp-uploads-private", "bob", "mp", "uploads", None),
        ("pc-upload", "bob", "mp-uploads-private", "bob", "pc", "uploads", None),
        ("pc-rough", "bob", "mp-rough-private", "bob", "pc", "rough", None),
        ("pc-generated", "bob", "private-root", "bob", "pc", "generated", "content_production"),
        ("mp-generated", "alice", "private-root", "alice", "mp", "generated", "image_design"),
        ("foreign-pc-upload", "charlie", "mp-uploads-private", "charlie", "pc", "uploads", None),
        ("foreign-pc-rough", "charlie", "mp-rough-private", "charlie", "pc", "rough", None),
        (
            "foreign-pc-generated",
            "charlie",
            "private-root",
            "charlie",
            "pc",
            "generated",
            "content_production",
        ),
        ("enterprise-rough", "bob", "rough-shared", enterprise_owner, "pc", "rough", None),
        ("enterprise-upload", "bob", "mp-uploads-shared", enterprise_owner, "pc", "uploads", None),
    ]
    async with session_factory() as db:
        db.add_all(categories)
        for asset_id, uid, category_id, category_owner, channel, folder, source in photos:
            tenant_id = "tenant-2" if uid == "charlie" else "tenant-1"
            metadata = {"source_channel": channel, "source_folder": folder}
            if source:
                metadata["source"] = source
            db.add(
                ContentCoverAsset(
                    id=asset_id,
                    owner_uid=uid,
                    tenant_id=tenant_id,
                    role="library_image",
                    original_file_name=f"{asset_id}.png",
                    content_type="image/png",
                    file_size=1,
                    image_width=1,
                    image_height=1,
                    sha256=asset_id,
                    bucket_name="image",
                    object_name=asset_id,
                    metadata_json=metadata,
                    created_at=(
                        datetime(2026, 9, 13, 16, 30)
                        if asset_id == "alice-rough"
                        else datetime(2026, 9, 14, 16, 30)
                        if asset_id == "pc-rough"
                        else datetime(2026, 9, 1)
                    ),
                )
            )
            db.add(
                ContentMaterialLibraryItem(
                    id=f"item-{asset_id}",
                    owner_uid=uid,
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    material_type="image",
                    display_name=asset_id,
                    category=category_id,
                    category_owner_uid=category_owner,
                    tags_json=[],
                    metadata_json=metadata,
                    status="enabled",
                )
            )
        db.add(
            ContentCoverAsset(
                id="alice-work",
                owner_uid="alice",
                role="output",
                original_file_name="work.png",
                content_type="image/png",
                file_size=1,
                image_width=1,
                image_height=1,
                sha256="alice-work",
                bucket_name="image",
                object_name="alice-work",
                metadata_json={},
                created_at=datetime(2026, 9, 16, 10, 2),
            )
        )
        db.add(
            ContentCoverJob(
                id="alice-job",
                owner_uid="alice",
                content_task_id="pc-task",
                mode="image2",
                status="succeeded",
                idempotency_key="alice-job",
                result_json={"asset_ids": ["alice-work"]},
            )
        )
        await db.commit()
        alice = SimpleNamespace(uid="alice", department_id="tenant-1", role="employee")
        bob = SimpleNamespace(uid="bob", department_id="tenant-1", role="employee")
        a_uploads = await list_folder(db, alice, "uploads", page=1, page_size=1)
        a_uploads_next = await list_folder(db, alice, "uploads", page=2, page_size=1)
        assert a_uploads["total"] == 2
        assert {item["asset_id"] for item in a_uploads["items"] + a_uploads_next["items"]} == {
            "alice-upload",
            "pc-upload",
        }
        assert {
            item["asset_id"] for item in (await list_folder(db, bob, "uploads", page=1, page_size=10))["items"]
        } == {"bob-upload", "pc-upload"}
        assert {
            item["asset_id"] for item in (await list_folder(db, alice, "rough", page=1, page_size=10))["items"]
        } == {"alice-rough", "pc-rough"}
        assert {item["asset_id"] for item in (await list_folder(db, bob, "rough", page=1, page_size=10))["items"]} == {
            "pc-rough"
        }
        assert {
            item["asset_id"] for item in (await list_folder(db, alice, "generated", page=1, page_size=10))["items"]
        } == {"pc-generated", "mp-generated"}
        rough_on_14 = await list_folder(
            db,
            alice,
            "rough",
            page=1,
            page_size=10,
            date_from=date(2026, 9, 14),
            date_to=date(2026, 9, 14),
        )
        assert rough_on_14["total"] == 1
        assert rough_on_14["items"][0]["asset_id"] == "alice-rough"
        assert rough_on_14["items"][0]["uploaded_at"].startswith("2026-09-13T16:30:00")
        assert (
            await list_folder(
                db,
                alice,
                "rough",
                page=1,
                page_size=10,
                date_from=date(2026, 9, 15),
                date_to=date(2026, 9, 15),
            )
        )["items"][0]["asset_id"] == "pc-rough"
        rough_page_1 = await list_folder(
            db,
            alice,
            "rough",
            page=1,
            page_size=1,
            date_from=date(2026, 9, 14),
            date_to=date(2026, 9, 15),
        )
        rough_page_2 = await list_folder(
            db,
            alice,
            "rough",
            page=2,
            page_size=1,
            date_from=date(2026, 9, 14),
            date_to=date(2026, 9, 15),
        )
        assert rough_page_1["total"] == rough_page_2["total"] == 2
        assert [rough_page_1["items"][0]["asset_id"], rough_page_2["items"][0]["asset_id"]] == [
            "pc-rough",
            "alice-rough",
        ]
        with pytest.raises(HTTPException) as error:
            await list_folder(
                db,
                alice,
                "rough",
                page=1,
                page_size=10,
                date_from=date(2026, 9, 15),
                date_to=date(2026, 9, 14),
            )
        assert error.value.status_code == 422
        folders = {folder["id"]: folder for folder in await folder_counts(db, alice)}
        counts = {folder_id: folder["count"] for folder_id, folder in folders.items()}
        assert counts == {"rough": 2, "generated": 2, "uploads": 2, "works": 1}
        assert folders["rough"]["cover_thumbnail_file_url"].endswith("/item-pc-rough/thumbnail")
        assert folders["generated"]["cover_thumbnail_file_url"].endswith("/item-pc-generated/thumbnail")
        assert folders["uploads"]["cover_thumbnail_file_url"].endswith("/item-pc-upload/thumbnail")
        assert folders["works"]["cover_file_url"].endswith("/alice-work/file")
        bob_folders = {folder["id"]: folder for folder in await folder_counts(db, bob)}
        assert bob_folders["works"]["cover_thumbnail_file_url"] is None
        assert bob_folders["works"]["cover_file_url"] is None
        works = await list_mp_works(db, SimpleNamespace(user=alice), page=1, page_size=10)
        assert works["total"] == counts["works"]
        assert works["items"][0]["uploaded_at"].startswith("2026-09-16T10:02:00")
        assert (await list_mp_works(db, SimpleNamespace(user=bob), page=1, page_size=10))["total"] == 0
        assert (
            await MaterialLibraryRepository(db, include_shared=True).get_item_for_user("item-bob-upload", "alice")
        ) is None
        shared = next(item for item in a_uploads["items"] + a_uploads_next["items"] if item["asset_id"] == "pc-upload")
        assert shared["can_manage"] is False
        assert "enterprise-rough" not in {
            item["asset_id"] for item in (await list_folder(db, alice, "rough", page=1, page_size=10))["items"]
        }
        assert "enterprise-upload" not in {
            item["asset_id"] for item in (await list_folder(db, alice, "uploads", page=1, page_size=10))["items"]
        }
    await engine.dispose()
