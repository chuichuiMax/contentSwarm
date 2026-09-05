from __future__ import annotations

import asyncio
import io
import base64
import uuid

import pytest
from PIL import Image

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 420), "#D9473F").save(output, format="PNG")
    return output.getvalue()


def _overlay_png(*, opaque: bool = False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGBA", (1080, 1440), "white" if opaque else (0, 0, 0, 0))
    if not opaque:
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1079, 1439), outline="#F0522D", width=28)
        draw.rectangle((0, 0, 1079, 180), fill="#252525")
    image.save(output, format="PNG")
    return output.getvalue()


async def test_cover_asset_upload_is_private_and_deletable(test_client, admin_headers, standard_user):
    bootstrap = await test_client.get("/api/content/covers/bootstrap", headers=admin_headers)
    assert bootstrap.status_code == 200, bootstrap.text
    body = bootstrap.json()
    assert {item["id"] for item in body["templates"]} >= {
        "grid_3x3",
        "split_vertical",
        "split_horizontal",
        "before_after",
        "card_stack",
        "hero_thumbs",
    }
    assert set(body["image2"]) == {
        "configured",
        "base_url",
        "api_key_configured",
        "model",
        "source",
        "can_manage",
        "quality",
        "verification_status",
        "verified_at",
        "capabilities",
        "modes",
    }
    assert body["image2"]["can_manage"] is True
    assert "api_key" not in body["image2"]

    image_bytes = _png()
    uploaded = await test_client.post(
        "/api/content/covers/assets",
        headers=admin_headers,
        data={"role": "source"},
        files={"file": ("source.png", image_bytes, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()["asset"]
    assert asset["role"] == "source"
    assert asset["content_type"] == "image/png"
    assert (asset["width"], asset["height"]) == (320, 420)
    material_library = await test_client.get(
        "/api/material-library/items?material_type=image",
        headers=admin_headers,
    )
    assert material_library.status_code == 200, material_library.text
    assert asset["id"] in {item["asset_id"] for item in material_library.json()["items"]}

    try:
        downloaded = await test_client.get(
            f"/api/content/covers/assets/{asset['id']}/file",
            headers=admin_headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        with Image.open(io.BytesIO(downloaded.content)) as result:
            assert result.size == (320, 420)
            assert result.format == "PNG"

        forbidden = await test_client.get(
            f"/api/content/covers/assets/{asset['id']}/file",
            headers=standard_user["headers"],
        )
        assert forbidden.status_code == 404

        invalid = await test_client.post(
            "/api/content/covers/assets",
            headers=admin_headers,
            data={"role": "source"},
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"]["error"]["code"] == "COVER_IMAGE_INVALID"
    finally:
        deleted = await test_client.delete(
            f"/api/content/covers/assets/{asset['id']}",
            headers=admin_headers,
        )
        assert deleted.status_code == 200, deleted.text

    missing = await test_client.get(
        f"/api/content/covers/assets/{asset['id']}/file",
        headers=admin_headers,
    )
    assert missing.status_code == 404


async def test_poster_template_library_preview_and_owner_isolation(
    test_client,
    admin_headers,
    standard_user,
):
    imported = await test_client.post(
        "/api/content/covers/poster-templates/import",
        headers=admin_headers,
        data={"category": "product_promotion"},
        files=[("files", ("alpha-board.png", _overlay_png(), "image/png"))],
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["summary"] == {"total": 1, "created": 1, "duplicate": 0, "failed": 0}
    template = imported.json()["items"][0]["template"]
    assert template["status"] == "needs_review"
    assert template["review_status"] == "pending"
    assert template["requires_review"] is True
    assert template["template_type"] == "alpha_overlay"
    assert template["category_name"] == "产品推广"
    assert "tags" not in template

    material_library = await test_client.get(
        "/api/material-library/items?material_type=cover_template",
        headers=admin_headers,
    )
    assert material_library.status_code == 200, material_library.text
    library_item = next(item for item in material_library.json()["items"] if item["asset_id"] == template["asset_id"])
    assert library_item["poster_template_id"] == template["id"]
    assert library_item["template_status"] == "needs_review"
    assert library_item["review_status"] == "pending"
    assert library_item["selectable"] is False

    blocked_product = await test_client.post(
        "/api/content/covers/assets",
        headers=admin_headers,
        data={"role": "source"},
        files={"file": ("blocked-product.png", _png(), "image/png")},
    )
    assert blocked_product.status_code == 201, blocked_product.text
    blocked_product_id = blocked_product.json()["asset"]["id"]
    blocked_preview = await test_client.post(
        "/api/content/covers/poster-billboard/preview",
        headers=admin_headers,
        json={"poster_template_id": template["id"], "product_asset_id": blocked_product_id},
    )
    assert blocked_preview.status_code == 409
    assert blocked_preview.json()["detail"]["error"]["code"] == "POSTER_TEMPLATE_NOT_READY"

    reviewed = await test_client.put(
        f"/api/content/covers/poster-templates/{template['id']}/review",
        headers=admin_headers,
        json={
            "version": template["version"],
            "product_box": template["product_box"],
            "text_slots": template["text_slots"],
            "confirm": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    template = reviewed.json()["template"]
    assert template["status"] == "ready"
    assert template["review_status"] == "confirmed"
    await test_client.delete(f"/api/content/covers/assets/{blocked_product_id}", headers=admin_headers)

    duplicate = await test_client.post(
        "/api/content/covers/poster-templates/import",
        headers=admin_headers,
        data={"category": "product_promotion"},
        files=[("files", ("same-board.png", _overlay_png(), "image/png"))],
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["summary"]["duplicate"] == 1

    listed = await test_client.get(
        "/api/content/covers/poster-templates?category=product_promotion&status=ready",
        headers=admin_headers,
    )
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == template["id"] for item in listed.json()["items"])

    managed = await test_client.patch(
        f"/api/content/covers/poster-templates/{template['id']}",
        headers=admin_headers,
        json={"name": "poster-library-fixture", "category": "marketing"},
    )
    assert managed.status_code == 200, managed.text
    template = managed.json()["template"]
    searched = await test_client.get(
        "/api/content/covers/poster-templates?query=poster-library-fixture",
        headers=admin_headers,
    )
    assert searched.status_code == 200, searched.text
    assert [item["id"] for item in searched.json()["items"]] == [template["id"]]

    analyzed = await test_client.post(
        f"/api/content/covers/poster-templates/{template['id']}/analyze",
        headers=admin_headers,
    )
    assert analyzed.status_code == 200, analyzed.text
    template = analyzed.json()["template"]
    assert template["status"] == "needs_review"
    assert template["review_status"] == "pending"
    reviewed = await test_client.put(
        f"/api/content/covers/poster-templates/{template['id']}/review",
        headers=admin_headers,
        json={
            "version": template["version"],
            "product_box": template["product_box"],
            "text_slots": template["text_slots"],
            "confirm": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    template = reviewed.json()["template"]
    assert template["status"] == "ready"

    private = await test_client.get(
        f"/api/content/covers/poster-templates/{template['id']}",
        headers=standard_user["headers"],
    )
    assert private.status_code == 404

    product_upload = await test_client.post(
        "/api/content/covers/assets",
        headers=admin_headers,
        data={"role": "source"},
        files={"file": ("product.png", _png(), "image/png")},
    )
    assert product_upload.status_code == 201, product_upload.text
    product = product_upload.json()["asset"]
    try:
        preview = await test_client.post(
            "/api/content/covers/poster-billboard/preview",
            headers=admin_headers,
            json={
                "poster_template_id": template["id"],
                "product_asset_id": product["id"],
                "transform": {"fit": "cover", "scale": 1, "focal_x": 0.5, "focal_y": 0.5},
            },
        )
        assert preview.status_code == 200, preview.text
        data_url = preview.json()["preview_data_url"]
        rendered = base64.b64decode(data_url.split(",", 1)[1])
        with Image.open(io.BytesIO(rendered)) as output:
            assert output.size == (1080, 1440)
            assert output.format == "PNG"
        assert preview.json()["quality_report"]["passed"] is True

        generated = await test_client.post(
            "/api/content/covers/poster-billboard/generate",
            headers=admin_headers,
            json={
                "poster_template_id": template["id"],
                "product_asset_id": product["id"],
                "transform": {"fit": "cover", "scale": 1, "focal_x": 0.5, "focal_y": 0.5},
                "enhance_with_image2": False,
                "n": 1,
                "idempotency_key": f"poster-{uuid.uuid4()}",
            },
        )
        assert generated.status_code == 202, generated.text
        job = generated.json()["job"]
        assert job["mode"] == "poster_billboard"
        for _ in range(40):
            current = await test_client.get(
                f"/api/content/covers/jobs/{job['id']}",
                headers=admin_headers,
            )
            assert current.status_code == 200, current.text
            job = current.json()["job"]
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.5)
        assert job["status"] == "succeeded", job
        assert len(job["result_assets"]) == 1
        output_response = await test_client.get(
            job["result_assets"][0]["file_url"],
            headers=admin_headers,
        )
        assert output_response.status_code == 200, output_response.text
        with Image.open(io.BytesIO(output_response.content)) as output:
            assert output.size == (1080, 1440)
            assert output.format == "PNG"
    finally:
        deleted_product = await test_client.delete(
            f"/api/content/covers/assets/{product['id']}",
            headers=admin_headers,
        )
        assert deleted_product.status_code == 200, deleted_product.text
        deleted_template = await test_client.delete(
            f"/api/content/covers/poster-templates/{template['id']}",
            headers=admin_headers,
        )
        assert deleted_template.status_code == 200, deleted_template.text
