"""远程素材库首次配置允许编辑服务地址。"""

import json
import secrets
import socket
import uuid
from urllib.parse import urlsplit

import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import delete

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_remote_material_first_config_can_edit_base_url():
    uid = f"rm_ui_{uuid.uuid4().hex}"
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password(secrets.token_urlsafe(24)),
            role="superadmin",
            department_id=department.id,
        )
        db.add(user)
        await db.commit()
        user_id = user.id
        department_id = department.id
        token = AuthUtils.create_access_token({"sub": str(user.id)})

    writes = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
            )
            try:
                context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                await context.add_init_script(f'localStorage.setItem("user_token", {json.dumps(token)})')
                page = await context.new_page()

                async def remote_material_api(route):
                    request = route.request
                    path = urlsplit(request.url).path
                    if path.endswith("/remote-config") and request.method == "GET":
                        await route.fulfill(
                            json={
                                "base_url": "http://default.example:8088",
                                "base_url_editable": True,
                                "configured": False,
                                "source": "environment",
                                "can_manage": True,
                                "verification_status": "unconfigured",
                                "verified_at": None,
                            }
                        )
                    elif path.endswith("/remote-config") and request.method == "PUT":
                        writes.append(request.post_data_json)
                        await route.fulfill(
                            json={
                                "base_url": request.post_data_json["base_url"],
                                "base_url_editable": False,
                                "configured": True,
                                "source": "database",
                                "can_manage": True,
                                "verification_status": "verified",
                                "verified_at": None,
                            }
                        )
                    elif path.endswith("/remote-sync/status"):
                        await route.fulfill(json={"job": None})
                    elif path.endswith("/remote-sync") and request.method == "POST":
                        await route.fulfill(
                            status=409,
                            json={"detail": {"error": {"code": "REMOTE_MATERIAL_SYNC_SKIPPED"}}},
                        )
                    else:
                        await route.continue_()

                await page.route("**/api/material-library/remote-*", remote_material_api)
                await page.goto("http://localhost:5173/materials/images")
                await page.get_by_role("button", name="同步远程素材").click()

                modal = page.locator(".ant-modal-content").filter(has_text="配置远程素材库")
                await expect(modal).to_be_visible()
                base_url = modal.get_by_role("textbox", name="远程地址")
                await expect(base_url).to_be_enabled()
                await expect(base_url).to_have_value("http://default.example:8088")
                await base_url.fill("http://custom.example:9090")
                await modal.get_by_role("textbox", name="账号").fill("remote-user")
                await modal.get_by_placeholder("请输入远程素材库密码").fill("remote-password")
                await modal.get_by_role("button", name="验证并保存").click()
                await expect(modal).to_be_hidden()

                assert writes == [
                    {
                        "base_url": "http://custom.example:9090",
                        "username": "remote-user",
                        "password": "remote-password",
                    }
                ]
                await context.close()
            finally:
                await browser.close()
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.execute(delete(Department).where(Department.id == department_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
