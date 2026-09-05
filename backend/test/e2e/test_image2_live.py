from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete, select

from yuxi.content_cover.image2_client import Image2Client, Image2Config
from yuxi.content_cover.schemas import Image2Input, Image2Request
from yuxi.repositories.content_cover_repository import ContentCoverRepository
from yuxi.services import content_cover_worker
from yuxi.storage.minio.client import get_minio_client
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_content import ContentCoverAsset, ContentCoverJob

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


def _fixture_image(*, template: bool = False) -> bytes:
    image = Image.new("RGB", (768, 1024), "#F6F0E8")
    draw = ImageDraw.Draw(image)
    if template:
        draw.rectangle((48, 48, 720, 360), fill="#D9473F")
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
        except OSError:
            title_font = ImageFont.load_default()
        draw.text((82, 150), "TEMPLATE TITLE", fill="white", font=title_font)
        draw.rectangle((48, 400, 450, 976), fill="#263238")
        draw.rectangle((480, 400, 720, 675), fill="#E7B24B")
        draw.rectangle((480, 705, 720, 976), fill="#4F78C8")
    else:
        draw.ellipse((150, 260, 618, 728), fill="#D9473F")
        draw.rectangle((220, 650, 548, 930), fill="#263238")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _fixture_mask() -> bytes:
    image = Image.new("L", (768, 1024), 0)
    ImageDraw.Draw(image).ellipse((150, 260, 618, 728), fill=255)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _request(mode: str) -> Image2Request:
    source = Image2Input(data=_fixture_image(), content_type="image/png", file_name="source.png")
    template = Image2Input(
        data=_fixture_image(template=True),
        content_type="image/png",
        file_name="template.png",
    )
    mask = Image2Input(data=_fixture_mask(), content_type="image/png", file_name="mask.png")
    common = {
        "mode": mode,
        "prompt": "生成简洁、主体突出的原创小红书 3:4 产品封面，不要水印或平台 Logo。",
        "size": "1080x1440",
        "n": 1,
    }
    if mode == "text_to_image":
        return Image2Request(**common)
    if mode == "image_to_image":
        return Image2Request(**common, source_images=[source])
    if mode == "multi_reference":
        return Image2Request(**common, source_images=[source], template_image=template)
    return Image2Request(**common, source_images=[source], mask_image=mask)


@pytest.mark.parametrize(
    "mode",
    ["text_to_image", "image_to_image", "multi_reference", "mask"],
)
async def test_live_image2_modes(mode: str):
    if os.getenv("RUN_IMAGE2_LIVE_TESTS", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set RUN_IMAGE2_LIVE_TESTS=1 to spend relay quota and run real image2 calls.")

    config = Image2Config.from_env()
    timeout_seconds = max(30.0, float(os.getenv("IMAGE2_POLL_TIMEOUT_SECONDS", "900")))
    interval_seconds = max(0.5, float(os.getenv("IMAGE2_POLL_INTERVAL_SECONDS", "2")))
    request = _request(mode)
    async with Image2Client(config) as client:
        result = await client.submit(request, idempotency_key=f"live-smoke-{mode}-{datetime.now(UTC).timestamp()}")
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while result.status == "pending" and asyncio.get_running_loop().time() < deadline:
            assert result.provider_task_id, "异步响应缺少 provider task ID"
            await asyncio.sleep(interval_seconds)
            result = await client.poll(result.provider_task_id)
        assert result.status == "completed", result.error_message or "image2 任务未完成"
        assert result.images, "image2 完成但未返回图片"
        outputs = [await client.read_output(item) for item in result.images]

    evidence_root = Path(os.getenv("IMAGE2_LIVE_OUTPUT_DIR", "saves/image2-live-smoke"))
    run_dir = evidence_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") / mode
    run_dir.mkdir(parents=True, exist_ok=True)
    output_files = []
    for index, (data, content_type) in enumerate(outputs, start=1):
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        suffix = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
        }.get(content_type, ".png")
        output_path = run_dir / f"result-{index}{suffix}"
        output_path.write_bytes(data)
        output_files.append(output_path.name)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": mode,
                "model": config.model,
                "size": request.size,
                "provider_task_id": result.provider_task_id,
                "output_files": output_files,
                "verified_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def test_live_worker_multi_reference_flow():
    """Exercise the real relay through the same Worker/DB/MinIO path used by the UI."""
    if os.getenv("RUN_IMAGE2_LIVE_TESTS", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("Set RUN_IMAGE2_LIVE_TESTS=1 to spend relay quota and run real image2 calls.")

    config = Image2Config.from_env()
    live_title = os.getenv("IMAGE2_LIVE_TITLE", "内容生产新方式")
    source_path = (os.getenv("IMAGE2_LIVE_SOURCE_PATH") or "").strip()
    template_path = (os.getenv("IMAGE2_LIVE_TEMPLATE_PATH") or "").strip()
    input_source = Path(source_path).read_bytes() if source_path else _fixture_image()
    input_template = Path(template_path).read_bytes() if template_path else _fixture_image(template=True)
    owner_uid = f"live_cover_{uuid.uuid4().hex}"
    job_id = f"ccj_{uuid.uuid4().hex}"
    stored_objects: list[tuple[str, str]] = []
    pg_manager.initialize()
    try:
        async with pg_manager.get_async_session_context() as db:
            repo = ContentCoverRepository(db)
            asset_ids: dict[str, str] = {}
            for role, data in (("source", input_source), ("template", input_template)):
                with Image.open(io.BytesIO(data)) as image:
                    image_width, image_height = image.size
                asset_id = f"cca_{uuid.uuid4().hex}"
                object_name = f"content-covers/{owner_uid}/{asset_id}/image.png"
                uploaded = await get_minio_client().aupload_file(
                    bucket_name=os.getenv("CONTENT_COVER_BUCKET", "content-covers"),
                    object_name=object_name,
                    data=data,
                    content_type="image/png",
                )
                stored_objects.append((uploaded.bucket_name, uploaded.object_name))
                await repo.create_asset(
                    id=asset_id,
                    owner_uid=owner_uid,
                    tenant_id=None,
                    content_task_id=None,
                    role=role,
                    original_file_name=f"{role}.png",
                    content_type="image/png",
                    file_size=len(data),
                    image_width=image_width,
                    image_height=image_height,
                    sha256=hashlib.sha256(data).hexdigest(),
                    bucket_name=uploaded.bucket_name,
                    object_name=uploaded.object_name,
                    metadata_json={"live_smoke": True},
                )
                asset_ids[role] = asset_id
            await repo.create_job(
                id=job_id,
                owner_uid=owner_uid,
                tenant_id=None,
                content_task_id=None,
                artifact_id=None,
                parent_job_id=None,
                mode="multi_reference",
                status="queued",
                model=config.model,
                provider_task_id=None,
                idempotency_key=f"live-worker-{uuid.uuid4().hex}",
                request_json={
                    "mode": "multi_reference",
                    "source_asset_ids": [asset_ids["source"]],
                    "template_asset_id": asset_ids["template"],
                    "mask_asset_id": None,
                    "title": live_title,
                    "prompt": (
                        "严格模板复刻。参考图1是原图，必须成为唯一底图并完整铺满画布；参考图2是模板。"
                        "把参考图2当作透明上层，原样迁移全部文字、字体、描边、阴影、贴纸、色块和圆角条，"
                        "保持它们的坐标、比例和颜色；不得保留模板底图，不得把原图缩进卡片或相框，"
                        "不得改写、翻译、模糊模板原文，也不得新增段落、口号、数字或其他文字。"
                    ),
                    "negative_prompt": "低清晰度、变形、复杂水印、模板旧底图残留、画中画、卡片套图",
                    "size": "1080x1440",
                    "n": 1,
                    "parameters": {"template_replicate": True},
                    "template_replicate": True,
                    "template_texts": {
                        "title": live_title,
                        "subtitle": "",
                        "tags": [],
                    },
                },
                result_json={},
                progress=0,
            )
            await db.commit()

        await content_cover_worker.process_content_cover_job({}, job_id)

        async with pg_manager.get_async_session_context() as db:
            job = await ContentCoverRepository(db).get_job(job_id)
            assert job is not None
            assert job.status == "succeeded", job.error_message
            assert job.model == config.model
            assert len(job.result_json.get("asset_ids") or []) == 1
            output = (
                await db.execute(
                    select(ContentCoverAsset).where(
                        ContentCoverAsset.id == job.result_json["asset_ids"][0],
                        ContentCoverAsset.owner_uid == owner_uid,
                    )
                )
            ).scalar_one()
            assert (output.image_width, output.image_height) == (1080, 1440)
            stored_objects.append((output.bucket_name, output.object_name))

        result_bytes = await get_minio_client().adownload_file(output.bucket_name, output.object_name)
        with Image.open(io.BytesIO(result_bytes)) as image:
            image.load()
            assert image.format == "PNG"
            assert image.size == (1080, 1440)
            assert image.mode == "RGB"

        evidence_root = Path(os.getenv("IMAGE2_LIVE_OUTPUT_DIR", "saves/image2-live-smoke"))
        run_dir = evidence_root / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") / "worker_multi_reference"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input-source.png").write_bytes(input_source)
        (run_dir / "input-template.png").write_bytes(input_template)
        (run_dir / "result-1.png").write_bytes(result_bytes)
        (run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "mode": "multi_reference",
                    "model": config.model,
                    "size": "1080x1440",
                    "job_id": job_id,
                    "provider_task_id": job.provider_task_id,
                    "verified_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        async with pg_manager.get_async_session_context() as db:
            rows = (
                await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))
            ).scalars()
            known_objects = {(item.bucket_name, item.object_name) for item in rows}
            stored_objects.extend(known_objects)
            await db.execute(delete(ContentCoverJob).where(ContentCoverJob.owner_uid == owner_uid))
            await db.execute(delete(ContentCoverAsset).where(ContentCoverAsset.owner_uid == owner_uid))
            await db.commit()
        for bucket_name, object_name in set(stored_objects):
            await get_minio_client().adelete_file(bucket_name, object_name)
        await pg_manager.close()
