from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.asyncio


def _ocr_test_image() -> bytes:
    image = Image.new("RGB", (900, 240), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=64)
    draw.text((40, 70), "OCR TEST 2026", fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def test_content_ocr_upload_persist_correct_and_read_for_authorized_user(
    test_client, admin_headers, standard_user
):
    bootstrap_response = await test_client.get("/api/content/bootstrap", headers=admin_headers)
    assert bootstrap_response.status_code == 200, bootstrap_response.text
    template = bootstrap_response.json()["industry_templates"][0]
    create_response = await test_client.post(
        "/api/content/tasks",
        headers=admin_headers,
        json={
            "industry_template_id": template["id"],
            "mode": "quick",
            "content_goal": template["default_goal"],
            "name": f"pytest_ocr_{uuid.uuid4().hex[:8]}",
        },
    )
    assert create_response.status_code == 200, create_response.text
    task_id = create_response.json()["task"]["id"]

    try:
        image_bytes = _ocr_test_image()
        upload_response = await test_client.post(
            f"/api/content/tasks/{task_id}/ocr-results",
            headers=admin_headers,
            files={"file": ("ocr-test.png", image_bytes, "image/png")},
        )
        assert upload_response.status_code == 200, upload_response.text
        item = upload_response.json()["item"]
        assert item["status"] == "completed", item
        assert item["raw_text"].strip()
        assert item["blocks"]
        assert all("confidence" in block and "box" in block for block in item["blocks"])
        assert item["source_image"]["file_name"] == "ocr-test.png"
        assert "bucket_name" not in item
        result_id = item["id"]

        list_response = await test_client.get(f"/api/content/tasks/{task_id}/ocr-results", headers=admin_headers)
        assert list_response.status_code == 200, list_response.text
        assert [record["id"] for record in list_response.json()["items"]] == [result_id]

        image_response = await test_client.get(f"/api/content/ocr-results/{result_id}/image", headers=admin_headers)
        assert image_response.status_code == 200, image_response.text
        assert image_response.headers["content-type"].startswith("image/png")
        assert image_response.content == image_bytes

        correction = "OCR TEST 2026（人工校对）"
        correction_response = await test_client.patch(
            f"/api/content/ocr-results/{result_id}",
            headers=admin_headers,
            json={"corrected_text": correction},
        )
        assert correction_response.status_code == 200, correction_response.text
        corrected = correction_response.json()["item"]
        assert corrected["raw_text"] == item["raw_text"]
        assert corrected["corrected_text"] == correction
        assert corrected["effective_text"] == correction

        retry_response = await test_client.post(f"/api/content/ocr-results/{result_id}/retry", headers=admin_headers)
        assert retry_response.status_code == 200, retry_response.text
        retried = retry_response.json()["item"]
        assert retried["status"] == "completed"
        assert retried["corrected_text"] is None
        assert retried["effective_text"] == retried["raw_text"]

        forbidden_response = await test_client.get(
            f"/api/content/ocr-results/{result_id}", headers=standard_user["headers"]
        )
        assert forbidden_response.status_code == 404

        invalid_response = await test_client.post(
            f"/api/content/tasks/{task_id}/ocr-results",
            headers=admin_headers,
            files={"file": ("not-image.png", b"not an image", "image/png")},
        )
        assert invalid_response.status_code == 400
        assert invalid_response.json()["detail"]["error"]["code"] == "OCR_IMAGE_INVALID"
    finally:
        delete_response = await test_client.delete(f"/api/content/tasks/{task_id}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text
