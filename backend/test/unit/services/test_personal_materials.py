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
async def test_fixed_folders_keep_mini_uploads_private_and_shared_images_visible():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner = "system:material-library"
    categories = [
        ContentMaterialCategory(
            owner_uid=owner,
            id="rough-shared",
            material_type="image",
            visibility="enterprise",
            name="毛坯房图库",
            image_design_role="rough",
        ),
        ContentMaterialCategory(
            owner_uid=owner, id="generated-shared", material_type="image", visibility="enterprise", name="生图图库"
        ),
        ContentMaterialCategory(
            owner_uid=owner, id="mp-uploads-shared", material_type="image", visibility="enterprise", name="我的上传"
        ),
    ]
    for uid in ("alice", "bob"):
        categories.extend(
            [
                ContentMaterialCategory(
                    owner_uid=uid, id="mp-rough-private", material_type="image", visibility="private", name="毛坯房图库"
                ),
                ContentMaterialCategory(
                    owner_uid=uid, id="mp-uploads-private", material_type="image", visibility="private", name="我的上传"
                ),
            ]
        )
    categories.append(
        ContentMaterialCategory(
            owner_uid="alice", id="legacy-generated", material_type="image", visibility="private", name="AI生图图库"
        )
    )
    photos = [
        ("alice-rough", "alice", "mp-rough-private", "alice"),
        ("alice-upload", "alice", "mp-uploads-private", "alice"),
        ("bob-upload", "bob", "mp-uploads-private", "bob"),
        ("pc-upload", "bob", "mp-uploads-shared", owner),
        ("pc-rough", "bob", "rough-shared", owner),
        ("generated", "bob", "generated-shared", owner),
        ("old-generated", "alice", "legacy-generated", "alice"),
    ]
    async with session_factory() as db:
        db.add_all(categories)
        for asset_id, uid, category_id, category_owner in photos:
            db.add(
                ContentCoverAsset(
                    id=asset_id,
                    owner_uid=uid,
                    role="library_image",
                    original_file_name=f"{asset_id}.png",
                    content_type="image/png",
                    file_size=1,
                    image_width=1,
                    image_height=1,
                    sha256=asset_id,
                    bucket_name="image",
                    object_name=asset_id,
                    metadata_json={"source": "image_design"} if asset_id == "old-generated" else {},
                    created_at=(
                        datetime(2026, 9, 13, 16, 30)
                        if asset_id == "alice-rough" else
                        datetime(2026, 9, 14, 16, 30)
                        if asset_id == "pc-rough" else datetime(2026, 9, 1)
                    ),
                )
            )
            db.add(
                ContentMaterialLibraryItem(
                    id=f"item-{asset_id}",
                    owner_uid=uid,
                    asset_id=asset_id,
                    material_type="image",
                    display_name=asset_id,
                    category=category_id,
                    category_owner_uid=category_owner,
                    tags_json=[],
                    metadata_json={"source": "image_design"} if asset_id == "old-generated" else {},
                    status="enabled",
                )
            )
        db.add(
            ContentCoverAsset(
                id="alice-work", owner_uid="alice", role="output", original_file_name="work.png",
                content_type="image/png", file_size=1, image_width=1, image_height=1, sha256="alice-work",
                bucket_name="image", object_name="alice-work", metadata_json={},
                created_at=datetime(2026, 9, 16, 10, 2),
            )
        )
        db.add(
            ContentCoverJob(
                id="alice-job", owner_uid="alice", content_task_id="pc-task", mode="image2",
                status="succeeded", idempotency_key="alice-job", result_json={"asset_ids": ["alice-work"]},
            )
        )
        await db.commit()
        alice = SimpleNamespace(uid="alice", role="employee")
        bob = SimpleNamespace(uid="bob", role="employee")
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
        assert (await list_folder(db, alice, "generated", page=1, page_size=10))["total"] == 1
        rough_on_14 = await list_folder(
            db, alice, "rough", page=1, page_size=10,
            date_from=date(2026, 9, 14), date_to=date(2026, 9, 14),
        )
        assert rough_on_14["total"] == 1
        assert rough_on_14["items"][0]["asset_id"] == "alice-rough"
        assert rough_on_14["items"][0]["uploaded_at"].startswith("2026-09-13T16:30:00")
        assert (await list_folder(
            db, alice, "rough", page=1, page_size=10,
            date_from=date(2026, 9, 15), date_to=date(2026, 9, 15),
        ))["items"][0]["asset_id"] == "pc-rough"
        rough_page_1 = await list_folder(
            db, alice, "rough", page=1, page_size=1,
            date_from=date(2026, 9, 14), date_to=date(2026, 9, 15),
        )
        rough_page_2 = await list_folder(
            db, alice, "rough", page=2, page_size=1,
            date_from=date(2026, 9, 14), date_to=date(2026, 9, 15),
        )
        assert rough_page_1["total"] == rough_page_2["total"] == 2
        assert [rough_page_1["items"][0]["asset_id"], rough_page_2["items"][0]["asset_id"]] == [
            "pc-rough", "alice-rough",
        ]
        with pytest.raises(HTTPException) as error:
            await list_folder(
                db, alice, "rough", page=1, page_size=10,
                date_from=date(2026, 9, 15), date_to=date(2026, 9, 14),
            )
        assert error.value.status_code == 422
        counts = {folder["id"]: folder["count"] for folder in await folder_counts(db, alice)}
        assert counts == {"rough": 2, "generated": 1, "uploads": 2, "works": 1}
        works = await list_mp_works(db, SimpleNamespace(user=alice), page=1, page_size=10)
        assert works["total"] == counts["works"]
        assert works["items"][0]["uploaded_at"].startswith("2026-09-16T10:02:00")
        assert (await list_mp_works(db, SimpleNamespace(user=bob), page=1, page_size=10))["total"] == 0
        assert (
            await MaterialLibraryRepository(db, include_shared=True).get_item_for_user("item-bob-upload", "alice")
        ) is None
        shared = next(item for item in a_uploads["items"] + a_uploads_next["items"] if item["asset_id"] == "pc-upload")
        assert shared["can_manage"] is False
    await engine.dispose()
