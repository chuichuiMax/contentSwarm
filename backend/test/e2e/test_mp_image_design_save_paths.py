"""Real API targets -> worker persistence -> PC and mini-program gallery reads.

Synthetic provider outputs avoid external image-generation cost; no provider or
real-device UI acceptance is implied by this persistence-chain test.
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test.integration.api import test_mp_image_design_router as mp_fixtures

from yuxi.image_design.schemas import ImageDesignSaveTarget
from yuxi.image_design.worker import attach_generated_asset
from yuxi.services.mp_service import authenticate_mp_request
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
async def test_fixed_paths_persist_all_workflows_in_selected_pc_and_mp_gallery(test_client, mp_accounts):
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    headers = mp_accounts[0]["headers"]
    asset_ids = []
    owner_uid = None
    try:
        async with sessions() as db:
            context = await authenticate_mp_request(db, headers["Authorization"])
            user = context.user
            owner_uid = user.uid
            pc_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(user.id)})}"}
            db.add(
                ContentMaterialCategory(
                    owner_uid=owner_uid, material_type="image", id="product", visibility="private", name="产品商品"
                )
            )
            await db.commit()

        response, folders = await asyncio.gather(
            test_client.get("/api/mp/image-design/save-targets", headers=headers),
            test_client.get("/api/mp/content/my-materials/folders", headers=headers),
        )
        assert folders.status_code == 200, folders.text
        assert response.status_code == 200, response.text
        scopes = response.json()["scopes"]
        assert [s["scope"] for s in scopes] == ["private", "enterprise"]
        assert scopes[0]["folders"][0]["id"] == "mp-generated-private"
        assert scopes[1]["folders"] and not scopes[1]["error"], scopes[1]
        for scope in scopes:
            gallery_id = scope["folders"][0]["id"]
            for workflow in ("style_transfer", "room_adapt", "cross_space"):
                asset_id = f"cca_save_path_{uuid.uuid4().hex}"
                asset_ids.append(asset_id)
                async with sessions() as db:
                    context = await authenticate_mp_request(db, headers["Authorization"])
                    asset = ContentCoverAsset(
                        id=asset_id,
                        owner_uid=owner_uid,
                        role="output",
                        original_file_name="generated.png",
                        content_type="image/png",
                        file_size=8,
                        image_width=32,
                        image_height=24,
                        sha256="a" * 64,
                        bucket_name="image",
                        object_name=f"test/{asset_id}.png",
                    )
                    db.add(asset)
                    await db.flush()
                    arguments = dict(
                        user=context.user,
                        asset=asset,
                        requested=ImageDesignSaveTarget(scope=scope["scope"], gallery_id=gallery_id),
                        job_id=f"idj_{uuid.uuid4().hex}",
                        workflow=workflow,
                        mp_fixed_target=True,
                    )
                    resolved, item = await attach_generated_asset(db, **arguments)
                    await db.commit()
                    again, retained = await attach_generated_asset(db, **arguments)
                    assert resolved.public_target == {"scope": scope["scope"], "gallery_id": gallery_id}
                    assert again == resolved and retained.id == item.id
                    assert item.owner_uid == owner_uid and item.category_owner_uid == resolved.category_owner_uid
                    references = list(
                        (
                            await db.scalars(
                                select(ImageDesignLibraryItem).where(ImageDesignLibraryItem.asset_id == asset_id)
                            )
                        ).all()
                    )
                    assert len(references) == 1
                    await db.commit()

                pc = await test_client.get(
                    "/api/material-library/items",
                    headers=pc_headers,
                    params={
                        "material_type": "image",
                        "scope": scope["scope"],
                        "category": gallery_id,
                        "page_size": 100,
                    },
                )
                assert pc.status_code == 200, pc.text
                assert item.id in {row["id"] for row in pc.json()["items"]}
                library = await test_client.get("/api/mp/image-design/library", headers=headers)
                assert library.status_code == 200, library.text
                assert asset_id in {row["asset_id"] for row in library.json()["items"]}
                if scope["scope"] == "private":
                    personal = await test_client.get("/api/mp/content/my-materials/generated", headers=headers)
                    assert personal.status_code == 200, personal.text
                    assert item.id in {row["id"] for row in personal.json()["items"]}
                else:
                    personal = await test_client.get("/api/mp/content/my-materials/generated", headers=headers)
                    assert item.id not in {row["id"] for row in personal.json()["items"]}

        async with sessions() as db:
            product = await db.scalar(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.owner_uid == owner_uid,
                    ContentMaterialCategory.id == "product",
                    ContentMaterialCategory.material_type == "image",
                )
            )
            assert product.name == "产品商品"
        other = await test_client.get("/api/mp/image-design/library", headers=mp_accounts[1]["headers"])
        assert other.status_code == 200, other.text
        assert not set(asset_ids).intersection(row["asset_id"] for row in other.json()["items"])
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
        await engine.dispose()
