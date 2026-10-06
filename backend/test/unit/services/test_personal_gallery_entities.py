from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.image_design.save_targets import can_contribute_to_category, resolve_mp_save_target
from yuxi.image_design.schemas import ImageDesignSaveTarget
from yuxi.repositories.material_library_repository import MaterialLibraryRepository
from yuxi.services.material_library_service import get_material_file, get_material_thumbnail, list_image_galleries
from yuxi.services.personal_materials import folder_categories, folder_counts, list_folder
from yuxi.storage.postgres.models_business import Base
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ContentMaterialFolderSetting,
)


@pytest.mark.asyncio
async def test_existing_gallery_ids_and_private_images_are_shared_by_pc_and_mini(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    alice = SimpleNamespace(uid="alice", department_id=1, role="user")
    bob = SimpleNamespace(uid="bob", department_id=2, role="user")
    async with factory() as db:
        db.add(ContentMaterialFolderSetting(folder_key="rough", name="旧虚拟入口", deleted_at=datetime(2026, 10, 1)))
        names = ("毛坯房图库", "我的上传", "AI生图图库", "我的作品")
        for owner in ("alice", "bob"):
            for index, name in enumerate(names):
                db.add(
                    ContentMaterialCategory(
                        owner_uid=owner,
                        material_type="image",
                        id=f"existing-{index}",
                        visibility="private",
                        name=name,
                        sort_order=index,
                    )
                )
        global_gallery = ContentMaterialCategory(
            owner_uid="super",
            material_type="image",
            id="global-gallery",
            visibility="private",
            name="超管新增",
            is_global_personal=True,
        )
        db.add(global_gallery)
        for owner, category, category_owner in (
            ("alice", "existing-1", "alice"),
            ("bob", "existing-1", "bob"),
            ("alice", "global-gallery", "super"),
            ("bob", "global-gallery", "super"),
            ("super", "global-gallery", "super"),
        ):
            key = f"{owner}-{category}"
            db.add(
                ContentCoverAsset(
                    id=key,
                    owner_uid=owner,
                    role="library_image",
                    original_file_name=f"{key}.png",
                    content_type="image/png",
                    file_size=1,
                    image_width=1,
                    image_height=1,
                    sha256=key,
                    bucket_name="image",
                    object_name=key,
                )
            )
            db.add(
                ContentMaterialLibraryItem(
                    id=key,
                    owner_uid=owner,
                    asset_id=key,
                    material_type="image",
                    display_name=key,
                    category=category,
                    category_owner_uid=category_owner,
                )
            )
        await db.commit()

        mapping = await folder_categories(db, alice)
        assert {key: rows[0].id for key, rows in mapping.items()} == {
            "rough": "existing-0",
            "uploads": "existing-1",
            "generated": "existing-2",
            "works": "existing-3",
        }
        await folder_categories(db, alice)
        assert (
            len(
                (
                    await db.scalars(
                        select(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid == "alice")
                    )
                ).all()
            )
            == 4
        )
        saved = await resolve_mp_save_target(db, alice, ImageDesignSaveTarget(scope="private"))
        assert saved.category_id == "existing-2"
        assert saved.category_owner_uid == "alice"
        assert can_contribute_to_category(alice, global_gallery)

        for user in (alice, bob, SimpleNamespace(uid="super", department_id=None, role="superadmin")):
            repo = MaterialLibraryRepository(db, include_shared=True)
            assert "global-gallery" in {c.id for c in await repo.list_categories(user.uid, "image")}
            own = await list_folder(db, user, "global-gallery", page=1, page_size=10)
            assert own["total"] == 1
            assert {row["asset_id"] for row in own["items"]} == {f"{user.uid}-global-gallery"}
            for other in {"alice", "bob", "super"} - {user.uid}:
                assert await repo.get_item_for_user(f"{other}-global-gallery", user.uid) is None
                for read in (get_material_file, get_material_thumbnail):
                    with pytest.raises(HTTPException) as denied:
                        await read(db, user, f"{other}-global-gallery")
                    assert denied.value.status_code == 404

        for user in (alice, bob):
            folders = {f["id"]: f for f in await folder_counts(db, user)}
            assert folders["uploads"]["count"] == 1
            assert folders["uploads"]["gallery_id"] == "existing-1"
            assert folders["global-gallery"]["count"] == 1
            galleries = (await list_image_galleries(db, user))["galleries"]
            own_upload = next(g for g in galleries if g.get("personal_folder") == "uploads")
            assert own_upload["count"] == 1
            assert own_upload["can_manage"] is False
            assert own_upload["can_upload"] is True
            global_card = next(g for g in galleries if g["id"] == "global-gallery")
            assert global_card["cover_item_id"] == f"{user.uid}-global-gallery"
            assert global_card["count"] == 1

        monkeypatch.setattr(
            "yuxi.services.material_library_service.read_material_bytes", AsyncMock(return_value=b"own")
        )
        assert (await get_material_file(db, alice, "alice-global-gallery"))[0] == b"own"
    await engine.dispose()
