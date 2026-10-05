"""员工原表导出及两种毛坯图库上传日期筛选。"""

import io
import os
import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from openpyxl import load_workbook
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentCoverAsset, ContentMaterialCategory, ContentMaterialLibraryItem
from yuxi.utils.auth_utils import AuthUtils
from yuxi.services.material_library_service import ensure_initial_enterprise_galleries


@pytest_asyncio.fixture
async def export_date_users():
    uid = f"ed_{uuid.uuid4().hex[:12]}"
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        await ensure_initial_enterprise_galleries(db)
        enterprise_root = (
            await db.execute(
                select(ContentMaterialCategory).where(
                    ContentMaterialCategory.visibility == "enterprise",
                    ContentMaterialCategory.parent_id.is_(None),
                    ContentMaterialCategory.image_design_role == "rough",
                )
            )
        ).scalar_one()
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        admin = User(uid=uid, username=uid, password_hash="unused", role="superadmin", department_id=department.id)
        member = User(
            uid=f"{uid}_m", username=f"{uid}_m", password_hash="unused", role="user", department_id=department.id
        )
        db.add_all([admin, member])
        await db.flush()
        admin_token = AuthUtils.create_access_token({"sub": str(admin.id)})
        member_token = AuthUtils.create_access_token({"sub": str(member.id)})
        user_ids = [admin.id, member.id]
        department_id = department.id
        for scope in ("private", "enterprise"):
            category_id = f"{uid}_{scope}"
            category_owner = uid if scope == "private" else enterprise_root.owner_uid
            db.add(
                ContentMaterialCategory(
                    owner_uid=category_owner,
                    id=category_id,
                    material_type="image",
                    visibility=scope,
                    tenant_id=str(department_id),
                    name=f"日期测试毛坯图库_{scope}",
                    image_design_role="rough",
                    parent_id=enterprise_root.id if scope == "enterprise" else None,
                )
            )
            # 上海 10 月 1 日的两侧边界，以及足够跨两页的当天素材。
            timestamps = (
                [datetime(2026, 9, 30, 15, 59, 59)]
                + [datetime(2026, 9, 30, 16) + timedelta(minutes=index) for index in range(25)]
                + [datetime(2026, 10, 1, 15, 59, 59), datetime(2026, 10, 1, 16)]
            )
            for index, timestamp in enumerate(timestamps):
                item_id = f"{category_id}_{index}"
                metadata = {"source_channel": "pc", "source_folder": "rough"}
                db.add(
                    ContentCoverAsset(
                        id=item_id,
                        owner_uid=uid,
                        tenant_id=str(department_id),
                        role="source",
                        bucket_name="image",
                        object_name=f"{item_id}.png",
                        original_file_name=f"{item_id}.png",
                        content_type="image/png",
                        file_size=1,
                        image_width=32,
                        image_height=24,
                        sha256="0" * 64,
                        created_at=timestamp,
                        metadata_json=metadata,
                    )
                )
                db.add(
                    ContentMaterialLibraryItem(
                        id=item_id,
                        owner_uid=uid,
                        category_owner_uid=category_owner,
                        tenant_id=str(department_id),
                        material_type="image",
                        asset_id=item_id,
                        category=category_id,
                        display_name=f"{item_id}.png",
                        status="enabled",
                        metadata_json=metadata,
                        created_at=datetime(2026, 10, 5),
                    )
                )
        await db.commit()
    try:
        yield {
            "uid": uid,
            "token": admin_token,
            "admin": {"Authorization": f"Bearer {admin_token}"},
            "member": {"Authorization": f"Bearer {member_token}"},
            "enterprise": f"{uid}_enterprise",
        }
    finally:
        async with session_factory() as db:
            for model in (ContentMaterialLibraryItem, ContentCoverAsset, ContentMaterialCategory):
                await db.execute(delete(model).where(model.owner_uid == uid))
            await db.execute(
                delete(ContentMaterialCategory).where(
                    ContentMaterialCategory.owner_uid == enterprise_root.owner_uid,
                    ContentMaterialCategory.id == f"{uid}_enterprise",
                )
            )
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_employee_export_contains_complete_list_and_enforces_permissions(test_client, export_date_users):
    headers = export_date_users["admin"]
    listing = await test_client.get("/api/employees", headers=headers)
    assert listing.status_code == 200, listing.text
    response = await test_client.get(
        "/api/employees/export",
        headers=headers,
        params={"keyword": "no-such-employee", "page": 99, "role": "no-such-role"},
    )
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    rows = list(load_workbook(io.BytesIO(response.content)).active.values)
    employees = listing.json()["employees"]
    assert len(rows) == len(employees) + 1
    assert [row[1] for row in rows[1:]] == [row["employee_code"] for row in employees]
    assert any(row[1] == export_date_users["uid"] and row[10] == 28 for row in rows[1:])
    denied = await test_client.get("/api/employees/export", headers=export_date_users["member"])
    assert denied.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["private", "enterprise"])
async def test_rough_date_range_uses_upload_time_and_keeps_total_and_pages(test_client, export_date_users, scope):
    params = {}
    if scope == "private":
        url = "/api/material-library/my-materials/rough"
    else:
        url = "/api/material-library/items"
        params = {"material_type": "image", "category": export_date_users["enterprise"], "scope": "enterprise"}
    params.update(date_from="2026-10-01", date_to="2026-10-01", page_size=24)
    pages = []
    for page in (1, 2):
        response = await test_client.get(url, headers=export_date_users["admin"], params={**params, "page": page})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 26
        pages.append(response.json()["items"])
    assert [len(page) for page in pages] == [24, 2]
    assert {row["id"] for page in pages for row in page} == {
        f"{export_date_users['uid']}_{scope}_{index}" for index in range(1, 27)
    }
    cleared = await test_client.get(
        url,
        headers=export_date_users["admin"],
        params={key: value for key, value in params.items() if key not in {"date_from", "date_to"}},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["total"] == 28
    reversed_range = await test_client.get(
        url, headers=export_date_users["admin"], params={**params, "date_from": "2026-10-02", "date_to": "2026-10-01"}
    )
    assert reversed_range.status_code == 422
