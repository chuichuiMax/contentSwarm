"""PC 个人素材仅展示实体图库，普通用户可上传但不能管理文件夹。"""

import json
import re
import secrets
import socket
import uuid

import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import delete

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, OperationLog, User
from yuxi.storage.postgres.models_content import ContentMaterialCategory
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_personal_materials_use_only_entity_and_custom_galleries():
    uid = f"material_ui_{uuid.uuid4().hex}"
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        admin_user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
            role="superadmin",
            department_id=department.id,
        )
        member_uid = f"{uid}_member"
        member_user = User(
            username=member_uid,
            uid=member_uid,
            password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
            role="user",
            department_id=department.id,
        )
        db.add_all([admin_user, member_user])
        await db.commit()
        user_ids = [admin_user.id, member_user.id]
        department_id = department.id
        admin_token = AuthUtils.create_access_token({"sub": str(admin_user.id)})
        member_token = AuthUtils.create_access_token({"sub": str(member_user.id)})

    custom_gallery = {
        "id": "custom-gallery",
        "code": "custom-gallery",
        "name": "客厅灵感",
        "description": "我的自建图库",
        "visibility": "private",
        "parent_id": None,
        "industry_slug": "decoration",
        "industry_name": "装修与家居",
        "is_system": False,
        "is_global_personal": True,
        "can_manage": True,
        "can_upload": True,
        "count": 0,
        "child_count": 0,
        "cover_item_id": None,
    }
    fixed_folders = [
        {"id": "generated", "name": "AI生图图库", "count": 2, "can_upload": False},
        {"id": "rough", "name": "毛坯房图库", "count": 1, "can_upload": True},
        {"id": "works", "name": "我的作品", "count": 3, "can_upload": False},
        {"id": "uploads", "name": "我的上传", "count": 4, "can_upload": True},
    ]

    entity_galleries = [
        {
            **custom_gallery,
            "id": f"entity-{f['id']}",
            "code": f"entity-{f['id']}",
            "name": f["name"],
            "personal_folder": f["id"],
            "can_manage": False,
            "can_edit": True,
            "can_delete": True,
            "can_upload": f["can_upload"],
            "is_global_personal": False,
            "count": f["count"],
        }
        for f in fixed_folders
    ]

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
            )
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(admin_token)})')
                page = await context.new_page()
                await page.route("**/api/auth/me", lambda route: route.fulfill(json={
                    "id": user_ids[0], "uid": uid, "username": uid, "role": "superadmin",
                    "department_id": department_id, "permission_scoped": False, "permissions": [],
                }))
                await page.route(
                    "**/api/material-library/my-materials/folders",
                    lambda route: route.fulfill(json={"folders": fixed_folders}),
                )
                await page.route(
                    re.compile(r"/api/material-library/my-materials/(rough|generated|uploads|works)(?:\?|$)"),
                    lambda route: route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 24}),
                )
                await page.route(
                    "**/api/material-library/galleries*",
                    lambda route: route.fulfill(
                        json={
                            "galleries": [*entity_galleries, custom_gallery],
                            "industries": [{"slug": "decoration", "name": "装修与家居"}],
                        }
                    ),
                )
                await page.route(
                    "**/api/material-library/categories?*",
                    lambda route: route.fulfill(
                        json={"categories": [*entity_galleries, custom_gallery], "can_create_shared": True}
                    ),
                )
                await page.route(
                    "**/api/material-library/items?*",
                    lambda route: route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 24}),
                )
                await page.route(
                    "**/api/material-library/remote-sync/status*",
                    lambda route: route.fulfill(json={"job": None}),
                )

                await page.goto("http://localhost:5173/materials/images", wait_until="domcontentloaded", timeout=60_000)
                await expect(page.get_by_role("heading", name="素材库")).to_be_visible(timeout=60_000)
                await expect(page.get_by_role("heading", name="固定图库", exact=True)).to_have_count(0)

                generated_card = page.locator("button.gallery-open").filter(has_text="生图图库")
                custom_card = page.locator("button.gallery-open").filter(has_text="客厅灵感")
                await expect(page.get_by_role("button", name="同步远程素材")).to_be_visible()
                await expect(page.get_by_role("button", name="新建图库")).to_be_visible()
                await expect(page.get_by_role("button", name="上传图片")).to_have_count(0)
                await expect(generated_card).to_be_visible()
                await expect(custom_card).to_be_visible()

                async def rename_preset(route):
                    assert route.request.method == "PATCH"
                    payload = route.request.post_data_json
                    assert payload["name"] == "施工照片"
                    entity_galleries[1]["name"] = payload["name"]
                    await route.fulfill(json={"category": entity_galleries[1]})

                await page.route("**/api/material-library/categories/entity-rough?*", rename_preset)
                for folder in fixed_folders:
                    edit_button = page.get_by_role("button", name=f"编辑图库 {folder['name']}", exact=True)
                    delete_button = page.get_by_role("button", name=f"删除图库 {folder['name']}", exact=True)
                    await expect(edit_button).to_be_visible()
                    await expect(delete_button).to_be_visible()
                await page.get_by_role("button", name="编辑图库 毛坯房图库", exact=True).click()
                edit_modal = page.locator(".ant-modal-content").filter(has_text="编辑图库")
                await expect(edit_modal).to_contain_text("所有账号")
                await expect(edit_modal).not_to_contain_text("可见范围")
                await edit_modal.get_by_placeholder("例如：春季新品素材").fill("施工照片")
                await edit_modal.get_by_role("button", name="保 存").click()
                await expect(page.get_by_role("button", name="编辑图库 施工照片", exact=True)).to_be_visible()
                await page.get_by_role("button", name="删除图库 施工照片", exact=True).click()
                delete_modal = page.locator(".ant-modal-content").filter(has_text="删除图库“施工照片”")
                await expect(delete_modal).to_contain_text("各账号的素材分别迁移")
                await expect(delete_modal.locator(".ant-select")).to_be_visible()
                await delete_modal.get_by_role("button", name="取 消").click()

                await page.locator(".ant-radio-button-wrapper").filter(has_text="企业共享").click()
                await expect(page.get_by_role("button", name="新建图库")).to_be_visible()
                await expect(page.get_by_role("button", name="上传图片")).to_have_count(0)
                await page.locator(".ant-radio-button-wrapper").filter(has_text="我的素材").click()

                await generated_card.click()
                await expect(page.get_by_role("button", name="新建图库")).to_have_count(0)
                await expect(page.get_by_role("button", name="新建二级图库")).to_have_count(0)
                await page.get_by_role("button", name="返回图库").click()

                await custom_card.click()
                await expect(page.get_by_role("button", name="新建二级图库")).to_be_visible()
                await page.get_by_role("button", name="上传图片").click()
                upload_modal = page.locator(".ant-modal-content").filter(has_text="上传素材图片")
                await expect(upload_modal).to_contain_text("客厅灵感")
                await context.close()

                member_context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                await member_context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(member_token)})')
                member_page = await member_context.new_page()
                await member_page.route("**/api/auth/me", lambda route: route.fulfill(json={
                    "id": user_ids[1], "uid": member_uid, "username": member_uid, "role": "user",
                    "department_id": department_id, "permission_scoped": False, "permissions": [],
                }))
                read_only_gallery = {**custom_gallery, "can_manage": False}
                member_entities = [{**gallery, "can_edit": False, "can_delete": False} for gallery in entity_galleries]
                await member_page.route(
                    "**/api/material-library/my-materials/folders",
                    lambda route: route.fulfill(json={"folders": fixed_folders}),
                )
                await member_page.route(
                    re.compile(r"/api/material-library/my-materials/(rough|generated|uploads|works)(?:\?|$)"),
                    lambda route: route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 24}),
                )
                await member_page.route(
                    "**/api/material-library/galleries*",
                    lambda route: route.fulfill(
                        json={
                            "galleries": [*member_entities, read_only_gallery],
                            "industries": [{"slug": "decoration", "name": "装修与家居"}],
                        }
                    ),
                )
                await member_page.route(
                    "**/api/material-library/categories?*",
                    lambda route: route.fulfill(
                        json={"categories": [*member_entities, read_only_gallery], "can_create_shared": False}
                    ),
                )
                await member_page.route(
                    "**/api/material-library/items?*",
                    lambda route: route.fulfill(json={"items": [], "total": 0, "page": 1, "page_size": 24}),
                )
                await member_page.route(
                    "**/api/material-library/remote-sync/status*",
                    lambda route: route.fulfill(json={"job": None}),
                )

                await member_page.goto("http://localhost:5173/materials/images")
                await expect(member_page.get_by_role("heading", name="素材库")).to_be_visible(timeout=15_000)
                member_gallery_card = member_page.locator("button.gallery-open").filter(has_text="客厅灵感")
                await expect(member_page.get_by_role("button", name="新建图库")).to_have_count(0)
                await expect(member_page.get_by_role("button", name="编辑图库 客厅灵感")).to_have_count(0)
                await expect(member_page.get_by_role("button", name="删除图库 客厅灵感")).to_have_count(0)
                await expect(member_page.get_by_role("button", name="编辑图库 施工照片")).to_have_count(0)
                await expect(member_page.get_by_role("button", name="删除图库 施工照片")).to_have_count(0)
                await expect(member_gallery_card).to_be_visible()
                await member_gallery_card.click()
                await expect(member_page.get_by_role("button", name="上传图片")).to_be_visible()
                await member_context.close()
            finally:
                await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await pg_manager.async_engine.dispose()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_real_preview_admin_and_member_see_expected_gallery_roots():
    uid = f"material_real_{uuid.uuid4().hex}"
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        users = [
            User(
                username=uid,
                uid=uid,
                password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
                role="superadmin",
                department_id=department.id,
            ),
            User(
                username=f"{uid}_member",
                uid=f"{uid}_member",
                password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
                role="user",
                department_id=department.id,
            ),
        ]
        db.add_all(users)
        await db.commit()
        user_ids = [user.id for user in users]
        user_uids = [user.uid for user in users]
        department_id = department.id
        tokens = [AuthUtils.create_access_token({"sub": str(user.id)}) for user in users]

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
            )
            try:
                for token, is_admin in zip(tokens, (True, False), strict=True):
                    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                    await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
                    page = await context.new_page()
                    async with page.expect_response(
                        lambda response: "/api/material-library/galleries" in response.url,
                        timeout=60_000,
                    ) as gallery_response:
                        await page.goto("http://localhost:5173/materials/images", wait_until="domcontentloaded")
                    response = await gallery_response.value
                    assert response.status == 200, await response.text()
                    await expect(page.get_by_role("heading", name="素材库")).to_be_visible(timeout=20_000)
                    for name in ("AI生图图库", "毛坯房图库", "我的作品", "我的上传"):
                        await expect(page.locator("button.gallery-open").filter(has_text=name)).to_be_visible()
                        await expect(page.locator("button.gallery-open").filter(has_text=name)).to_have_count(1)
                    await expect(page.get_by_role("button", name="上传图片")).to_have_count(0)
                    await expect(page.get_by_role("button", name="新建图库")).to_have_count(1 if is_admin else 0)
                    await expect(page.get_by_role("heading", name="固定图库", exact=True)).to_have_count(0)
                    await page.locator("button.gallery-open").filter(has_text="毛坯房图库").click()
                    await page.get_by_role("button", name="上传图片").click()
                    modal = page.locator(".ant-modal-content").filter(has_text="上传素材图片")
                    await expect(modal).to_be_visible()
                    await expect(modal).not_to_contain_text("请选择设计风格")
                    await modal.get_by_role("button", name="取 消").click()
                    await page.get_by_role("button", name="返回图库").click()

                    await page.locator(".ant-radio-button-wrapper").filter(has_text="企业共享").click()
                    await expect(page.locator("button.gallery-open").filter(has_text="案例图库")).to_be_visible()
                    await expect(page.locator("button.gallery-open").filter(has_text="生图图库")).to_be_visible()
                    await context.close()
            finally:
                await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(ContentMaterialCategory).where(ContentMaterialCategory.owner_uid.in_(user_uids)))
            await db.execute(delete(OperationLog).where(OperationLog.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
