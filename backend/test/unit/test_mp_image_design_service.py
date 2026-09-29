from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from yuxi.image_design.mp_schemas import MpLibraryCreate

from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ImageDesignLibraryItem,
    ImageDesignMpDraft,
    ImageDesignAnalysis,
)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (
            ContentCoverAsset,
            ContentMaterialCategory,
            ContentMaterialLibraryItem,
            ImageDesignLibraryItem,
            ImageDesignMpDraft,
            ImageDesignAnalysis,
        ):
            await connection.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def user(uid="employee"):
    return SimpleNamespace(uid=uid, department_id=1)


async def seed_material(db, *, visibility="enterprise", status="enabled"):
    category = ContentMaterialCategory(
        owner_uid="other",
        material_type="image",
        id="folder",
        name="Folder",
        visibility=visibility,
    )
    asset = ContentCoverAsset(
        id="asset",
        owner_uid="other",
        role="library_image",
        original_file_name="room.png",
        content_type="image/png",
        file_size=10,
        image_width=32,
        image_height=32,
        sha256="a" * 64,
        bucket_name="test",
        object_name="room.png",
    )
    item = ContentMaterialLibraryItem(
        id="source",
        owner_uid="other",
        asset_id="asset",
        category="folder",
        category_owner_uid="other",
        material_type="image",
        display_name="Room",
        status=status,
    )
    db.add_all([category, asset, item])
    await db.commit()
    return category, asset, item


def test_draft_schema_normalizes_slots_and_preserves_image_ids():
    from yuxi.image_design.mp_schemas import MpDraftsUpdate

    payload = MpDraftsUpdate.model_validate({"drafts": {"adapt": {"reference": {"id": "design-1"}}}})
    assert payload.model_dump()["drafts"] == {
        "redesign": {},
        "adapt": {"reference": {"id": "design-1"}},
        "transfer": {},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"owner_uid": "victim", "drafts": {}},
        {"drafts": {"unknown": {}}},
        {"drafts": {"adapt": []}},
    ],
)
def test_draft_schema_rejects_owner_and_malformed_workflows(payload):
    from yuxi.image_design.mp_schemas import MpDraftsUpdate

    with pytest.raises(ValidationError):
        MpDraftsUpdate.model_validate(payload)


@pytest.mark.parametrize("source_role", ["source", "reference", "rough"])
def test_library_schema_accepts_each_workflow_source_role(source_role):
    from yuxi.image_design.mp_schemas import MpLibraryCreate

    payload = MpLibraryCreate(source_library_item_id="source", source_role=source_role)
    assert payload.source_role == source_role


async def test_drafts_read_and_replace_only_current_owner(db):
    from yuxi.image_design.mp_schemas import MpDraftsUpdate
    from yuxi.image_design.mp_service import get_drafts, save_drafts

    db.add(ImageDesignMpDraft(owner_uid="victim", drafts_json={"adapt": {"description": "private"}}))
    await db.commit()
    assert (await get_drafts(db, user()))["drafts"] == {"redesign": {}, "adapt": {}, "transfer": {}}
    payload = MpDraftsUpdate(drafts={"adapt": {"reference": {"id": "input-1"}}})
    await save_drafts(db, user(), payload)
    await save_drafts(db, user(), payload)
    assert (await get_drafts(db, user()))["drafts"]["adapt"]["reference"]["id"] == "input-1"
    assert (await get_drafts(db, user("victim")))["drafts"]["adapt"] == {"description": "private"}
    assert await db.scalar(select(func.count()).select_from(ImageDesignMpDraft)) == 2


async def test_library_deduplicates_owner_asset_and_keeps_owners_separate(db):
    from yuxi.image_design.mp_schemas import MpLibraryCreate
    from yuxi.image_design.mp_service import add_library_item, list_library

    await seed_material(db)
    payload = MpLibraryCreate(source_library_item_id="source", source_gallery_id="folder", source_role="reference")
    first = (await add_library_item(db, user(), payload))["item"]
    again = (await add_library_item(db, user(), payload))["item"]
    other = (await add_library_item(db, user("other"), payload))["item"]
    assert first["id"] == again["id"] != other["id"]
    assert first["file_url"] == f"/api/mp/image-design/library/{first['id']}/file"
    assert first["thumbnail_file_url"] == f"/api/mp/image-design/library/{first['id']}/thumbnail"
    assert first["asset_id"] == "asset"
    assert first["source_item_id"] == "source"
    assert [item["id"] for item in (await list_library(db, user(), page=1, page_size=1))["items"]] == [first["id"]]
    assert (await list_library(db, user(), page=2, page_size=1))["items"] == []
    assert await db.scalar(select(func.count()).select_from(ImageDesignLibraryItem)) == 2


async def test_generated_material_entry_is_visible_in_image_design_library(db):
    from yuxi.image_design.mp_service import list_library

    category = ContentMaterialCategory(
        owner_uid="employee",
        material_type="image",
        id="private-root",
        name="我的素材（根目录）",
        visibility="private",
        is_system=True,
    )
    asset = ContentCoverAsset(
        id="generated-asset",
        owner_uid="employee",
        role="output",
        original_file_name="generated.png",
        content_type="image/png",
        file_size=10,
        image_width=32,
        image_height=32,
        sha256="b" * 64,
        bucket_name="test",
        object_name="generated.png",
        metadata_json={"domain": "image_design"},
    )
    material = ContentMaterialLibraryItem(
        id="generated-material",
        owner_uid="employee",
        asset_id=asset.id,
        category=category.id,
        category_owner_uid=category.owner_uid,
        material_type="image",
        display_name="Generated",
        status="enabled",
    )
    entry = ImageDesignLibraryItem(
        id="generated-library-entry",
        owner_uid="employee",
        tenant_id="1",
        asset_id=asset.id,
        source_material_item_id=material.id,
        source_gallery_id=category.id,
        source_role="generated",
    )
    db.add_all([category, asset, material, entry])
    await db.commit()

    items = (await list_library(db, user(), page=1, page_size=100))["items"]
    assert [(item["id"], item["source_role"], item["source_item_id"]) for item in items] == [
        (entry.id, "generated", material.id)
    ]


async def test_hiding_library_reference_preserves_pc_asset_and_task_input_and_can_be_readded(db):
    from yuxi.image_design.mp_service import (
        add_library_item, remove_library_item, list_library, get_library_item_and_asset,
    )
    _, asset, material = await seed_material(db)
    payload = MpLibraryCreate(source_library_item_id=material.id, source_role="reference")
    entry = (await add_library_item(db, user(), payload))["item"]
    with pytest.raises(HTTPException):
        await remove_library_item(db, user("someone-else"), entry["id"])
    await remove_library_item(db, user(), entry["id"])
    await remove_library_item(db, user(), entry["id"])
    assert (await list_library(db, user(), page=1, page_size=30))["total"] == 0
    assert (await get_library_item_and_asset(db, user(), entry["id"]))[1].id == asset.id
    assert material.deleted_at is None and asset.deleted_at is None
    assert (await add_library_item(db, user(), payload))["item"]["id"] == entry["id"]
    assert (await list_library(db, user(), page=1, page_size=30))["total"] == 1


async def test_library_http_flow_preserves_pc_material_and_enforces_account_boundary(db):
    import httpx
    from fastapi import FastAPI
    from server.routers.mp_image_design_router import mp_image_design
    from server.utils.auth_middleware import get_db, get_mp_context

    _, asset, material = await seed_material(db)
    current = user()
    app = FastAPI()
    app.include_router(mp_image_design, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_mp_context] = lambda: SimpleNamespace(user=current)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        payload = {"source_library_item_id": material.id, "source_role": "reference"}
        added = await client.post("/api/mp/image-design/library", json=payload)
        assert added.status_code == 200, added.text
        item_id = added.json()["item"]["id"]
        listed = await client.get("/api/mp/image-design/library?page=1&page_size=1")
        assert listed.status_code == 200 and listed.json()["total"] == 1
        assert listed.json()["items"][0]["recognized_roles"] == []
        current = user("someone-else")
        assert (await client.delete(f"/api/mp/image-design/library/{item_id}")).status_code == 404
        current = user()
        assert (await client.delete(f"/api/mp/image-design/library/{item_id}")).status_code == 200
        assert (await client.get("/api/mp/image-design/library")).json()["total"] == 0
        assert material.deleted_at is None and asset.deleted_at is None
        assert (await client.post("/api/mp/image-design/library", json=payload)).json()["item"]["id"] == item_id


async def test_library_recognition_uses_current_owner_hash_schema_and_completed_role(db):
    from yuxi.image_design.mp_service import add_library_item, list_library
    from yuxi.image_design.schemas import ANALYSIS_SCHEMA_VERSION
    _, asset, material = await seed_material(db)
    await add_library_item(db, user(), MpLibraryCreate(source_library_item_id=material.id, source_role="reference"))
    for number, (owner, sha, version, status, role) in enumerate([
        ("employee", asset.sha256, ANALYSIS_SCHEMA_VERSION, "completed", "style_reference"),
        ("other", asset.sha256, ANALYSIS_SCHEMA_VERSION, "completed", "structure_source"),
        ("employee", "wrong-hash", ANALYSIS_SCHEMA_VERSION, "completed", "structure_source"),
        ("employee", asset.sha256, 0, "completed", "structure_source"),
        ("employee", asset.sha256, ANALYSIS_SCHEMA_VERSION, "failed", "structure_source"),
    ]):
        db.add(ImageDesignAnalysis(id=f"a{number}", owner_uid=owner, material_item_id=material.id,
            asset_sha256=sha, schema_version=version, status=status, analysis_role=role,
            model_spec="test-model", cache_key=f"key-{number}"))
    await db.commit()
    items = (await list_library(db, user(), page=1, page_size=30))["items"]
    assert items[0]["recognized_roles"] == ["style_reference"]


async def test_private_root_migration_preserves_normal_uncategorized_images(db):
    from yuxi.repositories.material_library_repository import MaterialLibraryRepository
    category, asset, ordinary = await seed_material(db, visibility="private")
    category.owner_uid = "employee"
    category.id = "uncategorized"
    ordinary.owner_uid = "employee"
    ordinary.category_owner_uid = "employee"
    ordinary.category = "uncategorized"
    extra_assets = [ContentCoverAsset(id=f"asset-{key}", owner_uid="employee", role="output",
        original_file_name=f"{key}.png", content_type="image/png", file_size=10, image_width=32,
        image_height=32, sha256=key * 64, bucket_name="test", object_name=f"{key}.png") for key in ['c', 'd']]
    db.add_all(extra_assets)
    await db.flush()
    generated = ContentMaterialLibraryItem(id="old-root", owner_uid="employee", asset_id=extra_assets[0].id,
        material_type="image", category="uncategorized", category_owner_uid="employee", display_name="Generated",
        metadata_json={"source": "image_design", "resolved_save_target": {"scope": "private", "gallery_id": None}})
    explicit_folder = ContentMaterialLibraryItem(
        id="explicit-folder", owner_uid="employee", asset_id=extra_assets[1].id,
        material_type="image", category="uncategorized", category_owner_uid="employee", display_name="Explicit",
        metadata_json={"source": "image_design", "resolved_save_target": {
            "scope": "private", "gallery_id": "uncategorized"}})
    entry = ImageDesignLibraryItem(id="old-library", owner_uid="employee", asset_id=extra_assets[0].id,
        source_material_item_id=generated.id, source_gallery_id="uncategorized", source_role="generated")
    db.add_all([generated, explicit_folder, entry])
    await db.commit()
    repo = MaterialLibraryRepository(db)
    await repo.migrate_generated_private_root("employee", "private-root")
    await repo.migrate_generated_private_root("employee", "private-root")
    assert generated.category == "private-root" and entry.source_gallery_id == "private-root"
    assert ordinary.category == explicit_folder.category == "uncategorized"
    generated.category = "uncategorized"  # A later explicit move must not be migrated again.
    explicit_folder.metadata_json = {"source": "image_design", "resolved_save_target": {"scope": "private"}}
    await db.flush()
    await repo.migrate_generated_private_root("employee", "private-root")
    assert generated.category == explicit_folder.category == "uncategorized"


@pytest.mark.parametrize(("visibility", "status"), [("private", "enabled"), ("enterprise", "disabled")])
async def test_library_rejects_inaccessible_or_disabled_source(db, visibility, status):
    from yuxi.image_design.mp_schemas import MpLibraryCreate
    from yuxi.image_design.mp_service import add_library_item

    await seed_material(db, visibility=visibility, status=status)
    with pytest.raises(HTTPException) as error:
        await add_library_item(db, user(), MpLibraryCreate(source_library_item_id="source", source_role="rough"))
    assert error.value.status_code == 404
    assert await db.scalar(select(func.count()).select_from(ImageDesignLibraryItem)) == 0


async def test_library_rejects_forged_gallery_and_owner(db):
    from yuxi.image_design.mp_schemas import MpLibraryCreate
    from yuxi.image_design.mp_service import add_library_item

    await seed_material(db)
    with pytest.raises(ValidationError):
        MpLibraryCreate(source_library_item_id="source", source_role="reference", owner_uid="victim")
    with pytest.raises(HTTPException) as error:
        await add_library_item(
            db,
            user(),
            MpLibraryCreate(
                source_library_item_id="source",
                source_gallery_id="forged",
                source_role="reference",
            ),
        )
    assert error.value.status_code == 422


async def test_existing_reference_rechecks_source_visibility_and_owner_on_read(db):
    from yuxi.image_design.mp_schemas import MpLibraryCreate
    from yuxi.image_design.mp_service import add_library_item, get_library_item_and_asset, list_library

    category, _, _ = await seed_material(db)
    item = (
        await add_library_item(
            db,
            user(),
            MpLibraryCreate(
                source_library_item_id="source",
                source_role="reference",
            ),
        )
    )["item"]
    with pytest.raises(HTTPException) as error:
        await get_library_item_and_asset(db, user("stranger"), item["id"])
    assert error.value.status_code == 404
    category.visibility = "private"
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await get_library_item_and_asset(db, user(), item["id"])
    assert error.value.status_code == 404
    assert (await list_library(db, user(), page=1, page_size=100))["items"] == []


async def test_upload_creates_input_asset_and_private_upload_reference(db, monkeypatch):
    import io
    from fastapi import UploadFile
    from PIL import Image
    from yuxi.image_design.mp_service import upload_image
    from yuxi.storage import minio

    class Storage:
        async def aupload_file(self, *, bucket_name, object_name, data, content_type):
            assert Image.open(io.BytesIO(data)).size == (32, 24)
            return SimpleNamespace(bucket_name=bucket_name, object_name=object_name)

    monkeypatch.setattr(minio, "get_minio_client", lambda: Storage())
    raw = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(raw, format="PNG")
    raw.seek(0)
    result = await upload_image(db, user(), UploadFile(file=raw, filename="room.png"), role="source")
    asset = await db.get(ContentCoverAsset, result["item"]["asset_id"])
    assert asset.role == "image_design_input"
    assert asset.owner_uid == "employee"
    assert result["item"]["source_role"] == "upload"
    assert await db.scalar(select(func.count()).select_from(ImageDesignLibraryItem)) == 1
    assert await db.scalar(select(func.count()).select_from(ContentMaterialLibraryItem)) == 1
