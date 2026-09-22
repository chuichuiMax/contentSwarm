"""真实 HTTP 验证本站企业共享图库与个人素材隔离。"""

import os

import pytest
from sqlalchemy import update, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from yuxi.storage.postgres.models_business import Department, User
from test.integration.api.test_material_library_router import material_users as material_users, _png

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_enterprise_gallery_sharing(test_client, material_users):
    owner, member = material_users["owner"], material_users["other"]
    # fixture 创建普通成员；只提升本测试创建的账号。
    me = await test_client.get("/api/auth/me", headers=owner)
    assert me.status_code == 200, me.text
    uid = me.json()["uid"]
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with async_sessionmaker(engine)() as db:
        await db.execute(update(User).where(User.uid == uid).values(role="admin"))
        await db.commit()
    await engine.dispose()
    gallery = await test_client.post(
        "/api/material-library/categories",
        headers=owner,
        json={
            "material_type": "image",
            "name": "企业共享测试",
            "visibility": "enterprise",
        },
    )
    assert gallery.status_code == 201, gallery.text
    category = gallery.json()["category"]
    assert category["visibility"] == "enterprise"
    created = await test_client.post(
        "/api/material-library/images/import",
        headers=owner,
        data={"category": category["id"]},
        files=[("files", ("shared.png", _png(), "image/png"))],
    )
    assert created.status_code == 201, created.text
    item = created.json()["items"][0]
    galleries = await test_client.get("/api/material-library/galleries", headers=member)
    assert any(g["id"] == category["id"] for g in galleries.json()["galleries"])
    listed = await test_client.get(
        "/api/material-library/items", headers=member, params={"material_type": "image", "category": category["id"]}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["id"] == item["id"]
    assert listed.json()["items"][0]["can_manage"] is False
    for route in ("file", "thumbnail"):
        response = await test_client.get(f"/api/material-library/items/{item['id']}/{route}", headers=member)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("image/")
    denied = await test_client.patch(
        f"/api/material-library/items/{item['id']}", headers=member, json={"name": "非法修改"}
    )
    assert denied.status_code == 403, denied.text
    denied = await test_client.post(
        "/api/material-library/categories",
        headers=member,
        json={"material_type": "image", "name": "非法共享", "visibility": "enterprise"},
    )
    assert denied.status_code == 403, denied.text
    own = await test_client.post(
        "/api/material-library/images/import",
        headers=member,
        data={"category": category["id"]},
        files=[("files", ("member.png", _png(), "image/png"))],
    )
    assert own.status_code == 201, own.text
    own_id = own.json()["items"][0]["id"]
    rejected = await test_client.patch(
        f"/api/material-library/categories/{category['id']}",
        headers=owner,
        params={"material_type": "image"},
        json={"visibility": "private"},
    )
    assert rejected.status_code == 409, rejected.text
    for target, expected_visibility in (("product", "private"), (category["id"], "enterprise")):
        moved = await test_client.patch(
            f"/api/material-library/items/{own_id}", headers=member, json={"category": target}
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["item"]["visibility"] == expected_visibility
        assert moved.json()["item"]["uploaded_by"] == own.json()["items"][0]["uploaded_by"]
    changed = await test_client.patch(
        f"/api/material-library/items/{own_id}", headers=member, json={"name": "成员素材"}
    )
    assert changed.status_code == 200, changed.text
    deleted = await test_client.delete(f"/api/material-library/items/{own_id}", headers=owner)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["object_deleted"] is False
    private = await test_client.post(
        "/api/material-library/images/import",
        headers=member,
        data={"category": "product"},
        files=[("files", ("private.png", _png(), "image/png"))],
    )
    assert private.status_code == 201, private.text
    private_id = private.json()["items"][0]["id"]
    denied = await test_client.get(f"/api/material-library/items/{private_id}/file", headers=owner)
    assert denied.status_code == 404, denied.text
    # 删除个人图库并迁入企业图库也必须留下共享历史，不能再物理删除原图。
    migrated = await test_client.request(
        "DELETE",
        "/api/material-library/categories/product",
        headers=member,
        params={"material_type": "image"},
        json={"target_category_id": category["id"]},
    )
    assert migrated.status_code == 200, migrated.text
    visible = await test_client.get(f"/api/material-library/items/{private_id}/file", headers=owner)
    assert visible.status_code == 200, visible.text
    removed = await test_client.delete(f"/api/material-library/items/{private_id}", headers=member)
    assert removed.status_code == 200, removed.text
    assert removed.json()["object_deleted"] is False
    await test_client.delete(f"/api/material-library/items/{item['id']}", headers=owner)


async def test_share_legacy_gallery_inherits_children_and_preserves_item_ids(test_client, material_users):
    owner, member = material_users["owner"], material_users["other"]
    me = (await test_client.get("/api/auth/me", headers=owner)).json()
    other = (await test_client.get("/api/auth/me", headers=member)).json()
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    async with async_sessionmaker(engine)() as db:
        await db.execute(update(User).where(User.uid == me["uid"]).values(role="admin"))
        # 本站所有已登录账号共享，不按部门分组。
        another = Department(name=f"pytest-sharing-other-{other['uid'][-10:]}")
        db.add(another)
        await db.flush()
        another_id = another.id
        await db.execute(update(User).where(User.uid == other["uid"]).values(department_id=another_id))
        await db.commit()
    await engine.dispose()
    try:
        child = await test_client.post(
            "/api/material-library/categories",
            headers=owner,
            json={
                "material_type": "image",
                "name": "私有子图库",
                "parent_id": "product",
            },
        )
        assert child.status_code == 201, child.text
        child_id = child.json()["category"]["id"]
        uploaded = await test_client.post(
            "/api/material-library/images/import",
            headers=owner,
            data={"category": child_id},
            files=[("files", ("child.png", _png(), "image/png"))],
        )
        item = uploaded.json()["items"][0]
        before = await test_client.get(f"/api/material-library/items/{item['id']}/file", headers=member)
        assert before.status_code == 404
        shared = await test_client.patch(
            "/api/material-library/categories/product?material_type=image",
            headers=owner,
            json={"visibility": "enterprise"},
        )
        assert shared.status_code == 200, shared.text
        new_id = shared.json()["category"]["id"]
        assert new_id.startswith("mlc_")
        galleries = (await test_client.get("/api/material-library/galleries", headers=member)).json()["galleries"]
        inherited = next(g for g in galleries if g["id"] == child_id)
        assert inherited["visibility"] == "enterprise"
        assert inherited["parent_id"] == new_id
        available = await test_client.get(f"/api/material-library/items/{item['id']}/file", headers=member)
        assert available.status_code == 200
        anonymous = await test_client.get(f"/api/material-library/items/{item['id']}/file")
        assert anonymous.status_code in (401, 403)
        created_share = await test_client.post(
            "/api/material-library/shares",
            headers=member,
            json={"item_ids": [item["id"]]},
        )
        assert created_share.status_code == 201, created_share.text
        share_token = created_share.json()["share"]["token"]
        public_share = await test_client.get(f"/api/material-library/shares/{share_token}")
        assert public_share.status_code == 200, public_share.text
        assert public_share.json()["share"]["images"][0]["file_name"] == "child.png"
        public_image = await test_client.get(f"/api/material-library/shares/{share_token}/images/1")
        assert public_image.status_code == 200, public_image.text
        assert public_image.headers["content-type"].startswith("image/")
        null_scope = await test_client.patch(
            f"/api/material-library/categories/{new_id}?material_type=image", headers=owner, json={"visibility": None}
        )
        assert null_scope.status_code == 422
        denied = await test_client.patch(
            f"/api/material-library/categories/{child_id}?material_type=image",
            headers=owner,
            json={"visibility": "private"},
        )
        assert denied.status_code == 403
        private = await test_client.patch(
            f"/api/material-library/categories/{new_id}?material_type=image",
            headers=owner,
            json={"visibility": "private"},
        )
        assert private.status_code == 200, private.text
        hidden = await test_client.get(f"/api/material-library/items/{item['id']}/file", headers=member)
        assert hidden.status_code == 404
        denied_share = await test_client.post(
            "/api/material-library/shares", headers=member, json={"item_ids": [item["id"]]}
        )
        assert denied_share.status_code == 404, denied_share.text
        await test_client.delete(f"/api/material-library/items/{item['id']}", headers=owner)
    finally:
        async with async_sessionmaker(engine)() as db:
            await db.execute(update(User).where(User.uid == other["uid"]).values(department_id=me["department_id"]))
            await db.execute(delete(Department).where(Department.id == another_id))
            await db.commit()
        await engine.dispose()
