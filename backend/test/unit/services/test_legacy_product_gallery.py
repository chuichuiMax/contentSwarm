from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from scripts.purge_legacy_product_gallery import purge, run

from yuxi.services.material_library_service import get_material_categories, list_image_galleries
from yuxi.services.mp_service import list_mp_galleries
from yuxi.storage.postgres.models_business import Base, User
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ImageDesignJob,
    ImageDesignLibraryItem,
)
from yuxi.storage.minio import StorageError
from yuxi.utils.datetime_utils import utc_now_naive


@pytest.mark.asyncio
async def test_cli_run_initializes_database_synchronously_and_defaults_to_preview(monkeypatch):
    from yuxi.storage.postgres.manager import pg_manager

    initialize = Mock()
    close = AsyncMock()
    session = AsyncMock()
    preview = AsyncMock()
    monkeypatch.setattr(pg_manager, "initialize", initialize)
    monkeypatch.setattr(pg_manager, "close", close)
    monkeypatch.setattr(pg_manager, "AsyncSession", Mock(return_value=session))
    monkeypatch.setattr("scripts.purge_legacy_product_gallery.purge", preview)

    await run(owner_uids=["alice"], apply=False)

    initialize.assert_called_once_with()
    preview.assert_awaited_once_with(session.__aenter__.return_value, owner_uids=["alice"], apply=False)
    close.assert_awaited_once_with()


@pytest_asyncio.fixture
async def product_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("yuxi.services.material_library_service.ensure_initial_enterprise_galleries", AsyncMock())
    monkeypatch.setattr("yuxi.services.material_library_service._industry_catalog", AsyncMock(return_value={}))
    async with sessions() as db:
        for owner, name in (("alice", "产品商品"), ("bob", "AI生图图库")):
            db.add(User(uid=owner, username=owner, password_hash="unused", role="superadmin"))
            db.add(
                ContentMaterialCategory(
                    owner_uid=owner,
                    material_type="image",
                    id="product",
                    name=name,
                    visibility="private",
                    is_system=True,
                )
            )
        for index in range(6):
            key = f"legacy-{index}"
            db.add(
                ContentCoverAsset(
                    id=key,
                    owner_uid="alice",
                    role="output",
                    original_file_name=f"{key}.png",
                    content_type="image/png",
                    file_size=1,
                    image_width=1,
                    image_height=1,
                    sha256=key,
                    bucket_name="image",
                    object_name=f"legacy/{key}.png",
                )
            )
            db.add(
                ContentMaterialLibraryItem(
                    id=key,
                    owner_uid="alice",
                    category_owner_uid="alice",
                    material_type="image",
                    category="product",
                    asset_id=key,
                    display_name=key,
                )
            )
            db.add(
                ImageDesignLibraryItem(
                    id=key,
                    owner_uid="alice",
                    asset_id=key,
                    source_material_item_id=key,
                    source_gallery_id="product",
                    source_role="generated",
                )
            )
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_pc_and_mini_omit_old_product_but_keep_reused_ai_gallery(product_db):
    db = product_db
    for owner, expected in (("alice", False), ("bob", True)):
        user = await db.scalar(select(User).where(User.uid == owner))
        pc = await list_image_galleries(db, user)
        mp = await list_mp_galleries(db, SimpleNamespace(user=user))
        categories = await get_material_categories(db, user, "image")
        for result, key in ((pc, "galleries"), (mp, "galleries"), (categories, "categories")):
            assert any(row["id"] == "product" for row in result[key]) is expected
            assert all(row["name"] != "产品商品" for row in result[key])
        assert any(row["name"] == "AI生图图库" for row in pc["galleries"])


@pytest.fixture
def purge_storage(monkeypatch):
    storage = SimpleNamespace(adelete_file=AsyncMock())
    cache = AsyncMock()
    monkeypatch.setattr("scripts.purge_legacy_product_gallery.get_minio_client", lambda: storage)
    monkeypatch.setattr("scripts.purge_legacy_product_gallery.delete_material_display_cache", cache)
    return storage, cache


@pytest.mark.asyncio
async def test_dry_run_does_not_delete_gallery_or_images(product_db, purge_storage):
    result = await purge(product_db, owner_uids=["alice"])
    assert len(result["galleries"][0]["images"]) == 6
    assert result["blockers"] == []
    assert await product_db.get(ContentMaterialCategory, ("alice", "image", "product"))
    assert (await product_db.get(ContentCoverAsset, "legacy-0")).deleted_at is None
    purge_storage[0].adelete_file.assert_not_called()
    purge_storage[1].assert_not_called()


@pytest.mark.asyncio
async def test_purge_deletes_all_images_including_previously_retained_and_is_idempotent(product_db, purge_storage):
    item = await product_db.get(ContentMaterialLibraryItem, "legacy-0")
    item.metadata_json = {"ever_shared": True, "retain_asset_on_delete": True}
    item.deleted_at = utc_now_naive()
    await product_db.commit()
    result = await purge(product_db, owner_uids=["alice", "bob"], apply=True)
    assert result["blockers"] == []
    assert len(result["galleries"]) == 1
    assert await product_db.get(ContentMaterialCategory, ("alice", "image", "product")) is None
    assert (await product_db.get(ContentMaterialCategory, ("bob", "image", "product"))).name == "AI生图图库"
    for index in range(6):
        assert (await product_db.get(ContentCoverAsset, f"legacy-{index}")).deleted_at
        assert (await product_db.get(ContentMaterialLibraryItem, f"legacy-{index}")).deleted_at
        assert (await product_db.get(ImageDesignLibraryItem, f"legacy-{index}")).hidden_at
    assert purge_storage[0].adelete_file.await_count == 12
    assert purge_storage[1].await_count == 6
    repeated = await purge(product_db, owner_uids=["alice"], apply=True)
    assert repeated["galleries"] == []
    assert purge_storage[0].adelete_file.await_count == 12


@pytest.mark.asyncio
async def test_purge_only_matches_selected_private_legacy_galleries(product_db, purge_storage):
    gallery = await product_db.get(ContentMaterialCategory, ("bob", "image", "product"))
    gallery.name = "产品商品"
    gallery.visibility = "enterprise"
    await product_db.commit()
    assert (await purge(product_db, owner_uids=["bob"], apply=True))["galleries"] == []
    assert await product_db.get(ContentMaterialCategory, ("alice", "image", "product"))
    assert await product_db.get(ContentMaterialCategory, ("bob", "image", "product"))
    purge_storage[0].adelete_file.assert_not_called()


@pytest.mark.asyncio
async def test_purge_blocks_other_owner_reference_before_any_storage_deletion(product_db, purge_storage):
    product_db.add(
        ImageDesignLibraryItem(
            id="outside",
            owner_uid="bob",
            asset_id="legacy-5",
            source_role="generated",
        )
    )
    await product_db.commit()
    with pytest.raises(ValueError, match="阻止项"):
        await purge(product_db, owner_uids=["alice"], apply=True)
    purge_storage[0].adelete_file.assert_not_called()
    assert await product_db.get(ContentMaterialCategory, ("alice", "image", "product"))


@pytest.mark.asyncio
async def test_storage_failure_does_not_commit_success(product_db, purge_storage):
    purge_storage[0].adelete_file.side_effect = StorageError("storage unavailable")
    with pytest.raises(StorageError):
        await purge(product_db, owner_uids=["alice"], apply=True)
    await product_db.rollback()
    assert await product_db.get(ContentMaterialCategory, ("alice", "image", "product"))
    assert (await product_db.get(ContentCoverAsset, "legacy-0")).deleted_at is None


@pytest.mark.asyncio
async def test_purge_requires_existing_explicit_owner(product_db, purge_storage):
    for owners in ([], ["missing"]):
        with pytest.raises(ValueError):
            await purge(product_db, owner_uids=owners, apply=True)
    purge_storage[0].adelete_file.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["child", "active_job"])
async def test_purge_blocks_children_and_active_design_jobs(product_db, purge_storage, blocker):
    if blocker == "child":
        product_db.add(
            ContentMaterialCategory(
                owner_uid="alice",
                material_type="image",
                id="child",
                parent_id="product",
                name="child",
                visibility="private",
            )
        )
    else:
        product_db.add(
            ImageDesignJob(
                id="active-job",
                owner_uid="alice",
                workflow="redesign",
                status="running",
                request_json={"images": [{"library_item_id": "legacy-5"}]},
                idempotency_key="active-job",
            )
        )
    await product_db.commit()
    preview = await purge(product_db, owner_uids=["alice"])
    assert preview["blockers"]
    with pytest.raises(ValueError, match="阻止项"):
        await purge(product_db, owner_uids=["alice"], apply=True)
    purge_storage[0].adelete_file.assert_not_called()
