from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw
from sqlalchemy import delete, select

import yuxi.services.content_cover_service as content_cover_service
from server.routers.content_cover_router import content_covers
from server.utils.auth_middleware import get_db, get_required_user
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import (
    ContentCoverAsset,
    ContentCoverPosterTemplate,
    ContentMaterialLibraryItem,
)


def _png(*, product: bool = False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (1080, 1440), "#C78A52" if product else "white")
    if not product:
        ImageDraw.Draw(image).rectangle((150, 160, 850, 300), fill="#222222")
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_uploaded_template_requires_review_before_generation(monkeypatch: pytest.MonkeyPatch):
    if pg_manager._initialized:
        await pg_manager.close()
    pg_manager.initialize()
    await pg_manager.create_business_tables()
    owner_uid = f"poster-review-{uuid.uuid4().hex}"
    user = SimpleNamespace(uid=owner_uid, department_id=None, role="user")
    stored_objects: list[tuple[str, str]] = []

    async def deterministic_analysis(image):
        del image
        slot = {
            "id": "slot-1",
            "role": "title",
            "source_text": "错误文字",
            "editable": True,
            "box": {"x": 0.14, "y": 0.11, "width": 0.66, "height": 0.1},
            "style": {
                "fill": "#222222",
                "stroke": None,
                "stroke_width_ratio": 0,
                "font_size_ratio": 0.08,
                "bold": True,
                "align": "center",
                "panel_fill": None,
                "panel_radius_ratio": 0.2,
            },
            "max_chars": 20,
            "max_lines": 1,
            "confidence": 0.71,
            "candidate_count": 3,
            "consensus_count": 1,
            "source_variant": "original#recall",
            "alternatives": ["正确文字"],
            "review_state": "recognized",
        }
        return {
            "processing_version": "test-poster-v3",
            "template_type": "layout_template",
            "canvas_width": 1080,
            "canvas_height": 1440,
            "product_box": {"x": 0, "y": 0, "width": 1, "height": 1},
            "background_mode": "full_canvas",
            "safe_area": {"x": 0.02, "y": 0.02, "width": 0.96, "height": 0.96},
            "text_slots": [slot],
            "fixed_regions": [],
            "editable_regions": [slot["box"]],
            "decoration_regions": [],
            "ocr_raw_layers": [
                {
                    "id": "raw-1",
                    "text": "错误文字",
                    "box": slot["box"],
                    "confidence": 0.71,
                    "candidate_count": 3,
                    "consensus_count": 1,
                    "source_variant": "original#recall",
                    "alternatives": ["正确文字"],
                }
            ],
            "recognition_metrics": {
                "raw_layer_count": 1,
                "final_layer_count": 1,
                "low_confidence_count": 1,
                "average_confidence": 0.71,
            },
            "review_status": "pending",
            "layout_fingerprint": "fixture",
            "status": "needs_review",
        }

    monkeypatch.setattr(content_cover_service, "_analyze_poster_image", deterministic_analysis)

    app = FastAPI()
    app.include_router(content_covers, prefix="/api")

    async def override_db():
        async with pg_manager.get_async_session_context() as db:
            yield db

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_required_user] = override_user

    template_id = None
    product_id = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            imported = await client.post(
                "/api/content/covers/poster-templates/import",
                data={"category": "product_promotion"},
                files=[("files", ("review.png", _png(), "image/png"))],
            )
            assert imported.status_code == 201, imported.text
            template = imported.json()["items"][0]["template"]
            template_id = template["id"]
            assert template["status"] == "needs_review"
            assert template["requires_review"] is True
            assert template["ocr_raw_layers"][0]["text"] == "错误文字"

            product = await client.post(
                "/api/content/covers/assets",
                data={"role": "source"},
                files={"file": ("product.png", _png(product=True), "image/png")},
            )
            assert product.status_code == 201, product.text
            product_id = product.json()["asset"]["id"]
            blocked = await client.post(
                "/api/content/covers/poster-billboard/preview",
                json={"poster_template_id": template_id, "product_asset_id": product_id},
            )
            assert blocked.status_code == 409

            corrected = template["text_slots"]
            corrected[0]["source_text"] = "正确文字"
            draft = await client.put(
                f"/api/content/covers/poster-templates/{template_id}/review",
                json={
                    "version": template["version"],
                    "product_box": template["product_box"],
                    "text_slots": corrected,
                    "confirm": False,
                },
            )
            assert draft.status_code == 200, draft.text
            template = draft.json()["template"]
            assert template["status"] == "needs_review"
            assert template["text_slots"][0]["review_state"] == "user_edited"
            assert template["ocr_raw_layers"][0]["text"] == "错误文字"

            stale = await client.put(
                f"/api/content/covers/poster-templates/{template_id}/review",
                json={
                    "version": 1,
                    "product_box": template["product_box"],
                    "text_slots": template["text_slots"],
                    "confirm": True,
                },
            )
            assert stale.status_code == 409

            confirmed = await client.put(
                f"/api/content/covers/poster-templates/{template_id}/review",
                json={
                    "version": template["version"],
                    "product_box": template["product_box"],
                    "text_slots": template["text_slots"],
                    "confirm": True,
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            template = confirmed.json()["template"]
            assert template["status"] == "ready"
            assert template["review_status"] == "confirmed"
            assert template["analysis"]["confirmed_layers"][0]["source_text"] == "正确文字"

        async with pg_manager.get_async_session_context() as db:
            material = (
                await db.execute(
                    select(ContentMaterialLibraryItem).where(
                        ContentMaterialLibraryItem.owner_uid == owner_uid,
                        ContentMaterialLibraryItem.material_type == "cover_template",
                    )
                )
            ).scalar_one()
            assert material.status == "enabled"
    finally:
        async with pg_manager.get_async_session_context() as db:
            assets = list(
                (await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))).scalars()
            )
            stored_objects = [(item.bucket_name, item.object_name) for item in assets]
            if template_id:
                await db.execute(delete(ContentCoverPosterTemplate).where(ContentCoverPosterTemplate.id == template_id))
            await db.execute(
                delete(ContentMaterialLibraryItem).where(ContentMaterialLibraryItem.owner_uid == owner_uid)
            )
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))
            await db.commit()
        for bucket_name, object_name in stored_objects:
            await get_minio_client().adelete_file(bucket_name, object_name)
        await pg_manager.close()
