"""跨账号共享图片进入真实 Worker 成图，下架后仍能读取已授权原图。"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from test.integration.conftest import test_client as test_client
from test.integration.api.test_material_library_router import material_users as material_users, _png
from yuxi.storage.postgres.models_business import User
from yuxi.services.content_photo_composition import resolve_photo_composition
from yuxi.content_cover.photo_composition import PhotoComposition

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_shared_images_render_and_survive_removal(test_client, material_users):
    admin, member = material_users["owner"], material_users["other"]
    me = (await test_client.get("/api/auth/me", headers=admin)).json()
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with async_sessionmaker(engine)() as db:
        await db.execute(update(User).where(User.uid == me["uid"]).values(role="admin"))
        await db.commit()
    await engine.dispose()
    gallery = await test_client.post(
        "/api/material-library/categories",
        headers=admin,
        json={
            "name": "跨账号成图测试",
            "material_type": "image",
            "visibility": "enterprise",
        },
    )
    assert gallery.status_code == 201, gallery.text
    uploaded = await test_client.post(
        "/api/material-library/images/import",
        headers=admin,
        data={"category": gallery.json()["category"]["id"]},
        files=[("files", ("one.png", _png(), "image/png")), ("files", ("two.png", _png(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    items = uploaded.json()["items"]
    bootstrap = (await test_client.get("/api/content/bootstrap", headers=member)).json()
    template = next(t for t in bootstrap["industry_templates"] if t["slug"] == "decoration")
    task_response = await test_client.post(
        "/api/content/tasks",
        headers=member,
        json={
            "industry_template_id": template["id"],
            "mode": "pro",
            "content_goal": template["default_goal"],
            "name": f"pytest_shared_brief_{uuid.uuid4().hex[:8]}",
        },
    )
    assert task_response.status_code == 200, task_response.text
    task_id = task_response.json()["task"]["id"]
    brief = await test_client.put(
        f"/api/content/tasks/{task_id}/brief",
        headers=member,
        json={
            "brief": {
                "visual_material": {"image_item_id": items[0]["id"]},
            }
        },
    )
    assert brief.status_code == 200, brief.text
    snapshot = brief.json()["task"]["brief"]["visual_material"]
    assert snapshot["image_asset_id"] == items[0]["asset_id"]
    member_uid = (await test_client.get("/api/auth/me", headers=member)).json()["uid"]
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with async_sessionmaker(engine)() as db:
        user = (await db.execute(select(User).where(User.uid == member_uid))).scalar_one()
        composition = await resolve_photo_composition(
            db,
            user,
            PhotoComposition(
                layout_id="grid-2",
                slots=[{"image_item_id": i["id"]} for i in items],
            ),
            items[0]["id"],
            complete=True,
        )
        assert [slot["asset_id"] for slot in composition["slots"]] == [i["asset_id"] for i in items]
        await db.commit()
    await engine.dispose()
    deleted_task = await test_client.delete(f"/api/content/tasks/{task_id}", headers=member)
    assert deleted_task.status_code == 200, deleted_task.text
    created = await test_client.post(
        "/api/content/covers/compose",
        headers=member,
        json={
            "asset_ids": [item["asset_id"] for item in items],
            "template_id": "split_vertical",
            "idempotency_key": f"pytest-shared-{uuid.uuid4().hex}",
        },
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job"]["id"]
    # API 已冻结授权，立即下架，Worker 必须仍能读到原图。
    for item in items:
        deleted = await test_client.delete(f"/api/material-library/items/{item['id']}", headers=admin)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["object_deleted"] is False
        hidden = await test_client.get(f"/api/material-library/items/{item['id']}/file", headers=member)
        assert hidden.status_code == 404
        retained = await test_client.get(f"/api/content/covers/assets/{item['asset_id']}/file", headers=member)
        assert retained.status_code == 200, retained.text
        bypass = await test_client.delete(f"/api/content/covers/assets/{item['asset_id']}", headers=admin)
        assert bypass.status_code == 409, bypass.text
    for _ in range(60):
        response = await test_client.get(f"/api/content/covers/jobs/{job_id}", headers=member)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        await asyncio.sleep(1)
    assert job["status"] == "succeeded", job
    image = await test_client.get(job["result_assets"][0]["file_url"], headers=member)
    assert image.status_code == 200, image.text
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
