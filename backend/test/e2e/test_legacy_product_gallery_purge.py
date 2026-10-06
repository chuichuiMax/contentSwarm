"""Disposable gallery -> dry-run -> purge -> real PC/MP reads and object storage."""

import io
import os
import uuid

import pytest
from PIL import Image
from scripts.purge_legacy_product_gallery import purge
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.integration.api import test_mp_image_design_router as mp_fixtures

from yuxi.services.material_upload_queue import material_thumb_object_name
from yuxi.services.mp_service import authenticate_mp_request
from yuxi.storage.minio import get_minio_client
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentMaterialCategory,
    ContentMaterialLibraryItem,
    ImageDesignLibraryItem,
)
from yuxi.utils.auth_utils import AuthUtils

material_users = mp_fixtures.material_users
mp_accounts = mp_fixtures.mp_accounts


@pytest.fixture
def test_client(e2e_client):
    return e2e_client


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_purge_removes_gallery_and_six_images_from_pc_mp_and_storage(test_client, mp_accounts):
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = get_minio_client()
    asset_ids, objects = [], []
    owner_uid = None
    mp_headers = mp_accounts[0]["headers"]
    image = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
    try:
        async with sessions() as db:
            context = await authenticate_mp_request(db, mp_headers["Authorization"])
            user = context.user
            owner_uid = user.uid
            pc_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
            db.add(
                ContentMaterialCategory(
                    owner_uid=owner_uid,
                    material_type="image",
                    id="product",
                    name="产品商品",
                    visibility="private",
                    is_system=True,
                )
            )
            for _ in range(6):
                key = f"purge_test_{uuid.uuid4().hex}"
                asset_ids.append(key)
                original = f"test/{key}.png"
                for object_name in (original, material_thumb_object_name(original)):
                    objects.append(object_name)
                    await storage.aupload_file("image", object_name, image.getvalue(), "image/png")
                db.add(
                    ContentCoverAsset(
                        id=key,
                        owner_uid=owner_uid,
                        role="output",
                        original_file_name="test.png",
                        content_type="image/png",
                        file_size=len(image.getvalue()),
                        image_width=2,
                        image_height=2,
                        sha256=key,
                        bucket_name="image",
                        object_name=original,
                    )
                )
                await db.flush()
                db.add(
                    ContentMaterialLibraryItem(
                        id=key,
                        owner_uid=owner_uid,
                        category_owner_uid=owner_uid,
                        material_type="image",
                        category="product",
                        asset_id=key,
                        display_name="purge test",
                        metadata_json={"ever_shared": True, "retain_asset_on_delete": True},
                    )
                )
                db.add(
                    ImageDesignLibraryItem(
                        id=key,
                        owner_uid=owner_uid,
                        asset_id=key,
                        source_material_item_id=key,
                        source_gallery_id="product",
                        source_role="generated",
                    )
                )
            await db.commit()
            preview = await purge(db, owner_uids=[owner_uid])
            assert len(preview["galleries"][0]["images"]) == 6 and not preview["blockers"]
        before = await test_client.get(f"/api/material-library/items/{asset_ids[0]}/file", headers=pc_headers)
        assert before.status_code == 200, before.text
        for path, headers in (
            ("/api/material-library/galleries", pc_headers),
            ("/api/mp/content/galleries", mp_headers),
        ):
            response = await test_client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            assert all(row["name"] != "产品商品" for row in response.json()["galleries"])
        async with sessions() as db:
            applied = await purge(db, owner_uids=[owner_uid], apply=True)
            assert not applied["blockers"]
            assert await db.get(ContentMaterialCategory, (owner_uid, "image", "product")) is None
            assets = list(await db.scalars(select(ContentCoverAsset).where(ContentCoverAsset.id.in_(asset_ids))))
            assert len(assets) == 6 and all(asset.deleted_at for asset in assets)
            assert (await purge(db, owner_uids=[owner_uid], apply=True))["galleries"] == []
        for object_name in objects:
            assert await storage.astat_file("image", object_name) is None
        for key in asset_ids:
            for path, headers in (
                (f"/api/material-library/items/{key}/file", pc_headers),
                (f"/api/material-library/items/{key}/thumbnail", pc_headers),
                (f"/api/mp/image-design/library/{key}/file", mp_headers),
            ):
                response = await test_client.get(path, headers=headers)
                assert response.status_code == 404, response.text
        library = await test_client.get("/api/mp/image-design/library", headers=mp_headers)
        assert library.status_code == 200, library.text
        assert not set(asset_ids).intersection(row["asset_id"] for row in library.json()["items"])
    finally:
        async with sessions() as db:
            await db.execute(delete(ImageDesignLibraryItem).where(ImageDesignLibraryItem.asset_id.in_(asset_ids)))
            await db.execute(
                delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.asset_id.in_(asset_ids))
            )
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.id.in_(asset_ids)))
            if owner_uid:
                await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid == owner_uid))
            await db.commit()
        for object_name in objects:
            await storage.adelete_file("image", object_name)
        await engine.dispose()
