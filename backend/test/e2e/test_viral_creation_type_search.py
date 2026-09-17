"""真实知识库配置、界面选择与文章精确检索；不调用模型，不修改用户资料。"""

import copy
import json
import secrets
import socket
import uuid

import httpx
import pytest
from patchright.async_api import async_playwright, expect
from sqlalchemy import delete, select

from yuxi.services.content_viral_assets import preparation_skill_hash, search_ready_viral_assets
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import Department, User
from yuxi.storage.postgres.models_content import ContentViralArticleVersion as Asset
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeFile
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_kb_mapping_persists_and_filters_reference_selection():
    pg_manager.initialize()
    uid = f"kb_mapping_{uuid.uuid4().hex}"
    password = secrets.token_urlsafe(24)
    kb_ids, asset_ids = [], []
    async with pg_manager.AsyncSession() as db:
        department = Department(name=uid)
        db.add(department)
        await db.flush()
        department_id = department.id
        user = User(
            username=uid,
            uid=uid,
            password_hash=AuthUtils.hash_password(password),
            role="admin",
            department_id=department_id,
        )
        db.add(user)
        await db.commit()
        user_id = user.id
        example = await db.scalar(
            select(Asset)
            .where(Asset.status == "ready", Asset.preparation_skill_hash == preparation_skill_hash())
            .limit(1)
        )
        assert example, "需要一篇已准备参考来验证检索"
        code = example.prepared_json["reference_card"]["content_type_code"]
        other_code = "CT06" if code != "CT06" else "CT04"
        source, prepared = copy.deepcopy(example.source_json), copy.deepcopy(example.prepared_json)
        embedding = await db.scalar(
            select(KnowledgeBase.embedding_model_spec).where(KnowledgeBase.kb_type == "milvus").limit(1)
        )
    try:
        async with httpx.AsyncClient(base_url="http://localhost:5050", timeout=60) as client:
            login = await client.post("/api/auth/token", data={"username": uid, "password": password})
            login.raise_for_status()
            token = login.json()["access_token"]
            client.headers["Authorization"] = f"Bearer {token}"
            names = []
            for suffix, mapped_type in (("matched", code), ("other", other_code)):
                response = await client.post(
                    "/api/knowledge/databases",
                    json={
                        "database_name": f"{uid}_{suffix}",
                        "description": "临时映射测试",
                        "kb_type": "milvus",
                        "embedding_model_spec": embedding,
                        "additional_params": {"viral_content_type": mapped_type},
                    },
                )
                response.raise_for_status()
                kb_id = response.json()["kb_id"]
                kb_ids.append(kb_id)
                info = (await client.get(f"/api/knowledge/databases/{kb_id}")).json()
                assert info["additional_params"]["viral_content_type"] == mapped_type
                names.append(info["name"])
            bad = await client.put(
                f"/api/knowledge/databases/{kb_ids[0]}",
                json={
                    "name": names[0],
                    "description": "",
                    "additional_params": {"viral_content_type": [code, other_code]},
                },
            )
            assert bad.status_code == 400
            async with pg_manager.AsyncSession() as db:
                for index, kb_id in enumerate(kb_ids):
                    for wrong_article in (False, True):
                        unique = uuid.uuid4().hex
                        file_id, asset_id = f"file_{unique}", f"vav_{unique}"
                        asset_ids.append(asset_id)
                        article_source = {**source, "kb_id": kb_id, "file_id": file_id, "source_file_version": unique}
                        article_prepared = copy.deepcopy(prepared)
                        article_prepared["reference_card"]["content_type_code"] = other_code if wrong_article else code
                        db.add(
                            KnowledgeFile(
                                file_id=file_id,
                                kb_id=kb_id,
                                filename=f"mapping-{unique}.txt",
                                content_hash=unique,
                                status="done",
                            )
                        )
                        await db.flush()
                        db.add(
                            Asset(
                                id=asset_id,
                                article_id=f"article_{unique}",
                                kb_id=kb_id,
                                file_id=file_id,
                                industry_slug="decoration",
                                source_hash=unique,
                                preparation_skill_hash=preparation_skill_hash(),
                                source_json=article_source,
                                prepared_json=article_prepared,
                                status="ready",
                                created_by=uid,
                                attempt=1,
                            )
                        )
                await db.commit()
                found = await search_ready_viral_assets(
                    db,
                    user,
                    industry_slug="decoration",
                    query=source["title"],
                    kb_ids=kb_ids,
                    content_type_code=code,
                    limit=10,
                )
                assert [item["id"] for item in found] == [asset_ids[0]]
                for query in ("与参考完全不同的楼盘zxqv987654", ""):
                    found = await search_ready_viral_assets(
                        db, user, industry_slug="decoration", query=query, content_type_code=code, limit=100
                    )
                    assert asset_ids[0] in {item["id"] for item in found}
                    assert not ({asset_ids[1], asset_ids[2], asset_ids[3]} & {item["id"] for item in found})
                # 没有指定任务类型时也不能跨越知识库与文章的类型边界。
                found = await search_ready_viral_assets(
                    db, user, industry_slug="decoration", query=source["title"], kb_ids=kb_ids, limit=10
                )
                assert {item["id"] for item in found} == {asset_ids[0], asset_ids[3]}
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
                )
                try:
                    context = await browser.new_context()
                    await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
                    page = await context.new_page()
                    await page.goto("http://localhost:5173/knowledge")
                    await page.get_by_role("button", name="新建知识库").click()
                    await expect(page.get_by_text("爆款创作类型", exact=True)).to_be_visible()
                    await page.get_by_role("button", name="取 消").click()
                    await page.goto(f"http://localhost:5173/knowledge/{kb_ids[0]}")
                    await expect(page.get_by_role("heading", name=names[0], exact=True)).to_be_visible()
                    await page.get_by_role("button", name="编辑", exact=True).click()
                    field = page.locator(".ant-form-item").filter(has=page.get_by_text("爆款创作类型", exact=True))
                    await field.locator(".ant-select").click()
                    await page.locator(".ant-select-dropdown:visible").get_by_text("日常工作", exact=True).click()
                    async with page.expect_response(
                        lambda response: response.request.method == "PUT" and response.url.endswith(kb_ids[0])
                    ) as saved:
                        await page.get_by_role("button", name="确 定", exact=True).click()
                    assert (await saved.value).status == 200
                    info = (await client.get(f"/api/knowledge/databases/{kb_ids[0]}")).json()
                    assert info["additional_params"]["viral_content_type"] == "CT07"
                    await page.goto("http://localhost:5173/content/admin/rules")
                    await page.get_by_text("爆款参考资产", exact=True).click()
                    await page.get_by_role("button", name="从已有文件准备").click()
                    modal = page.get_by_role("dialog")
                    await modal.locator(".ant-select").nth(0).click()
                    await page.locator(".ant-select-dropdown:visible").get_by_text("日常工作", exact=True).click()
                    await modal.locator(".ant-select").nth(1).click()
                    dropdown = page.locator(".ant-select-dropdown:visible")
                    await expect(dropdown.get_by_text(names[0], exact=True)).to_be_visible()
                    await expect(dropdown.get_by_text(names[1], exact=True)).to_have_count(0)
                finally:
                    await browser.close()
            response = await client.put(
                f"/api/knowledge/databases/{kb_ids[0]}",
                json={"name": names[0], "description": "", "additional_params": {"viral_content_type": None}},
            )
            response.raise_for_status()
            info = (await client.get(f"/api/knowledge/databases/{kb_ids[0]}")).json()
            assert info["additional_params"]["viral_content_type"] is None
    finally:
        async with pg_manager.AsyncSession() as db:
            await db.execute(delete(Asset).where(Asset.id.in_(asset_ids)))
            await db.execute(delete(KnowledgeFile).where(KnowledgeFile.kb_id.in_(kb_ids)))
            await db.commit()
        if kb_ids:
            async with httpx.AsyncClient(
                base_url="http://localhost:5050", headers={"Authorization": f"Bearer {token}"}, timeout=60
            ) as client:
                for kb_id in kb_ids:
                    response = await client.delete(f"/api/knowledge/databases/{kb_id}")
                    response.raise_for_status()
        async with pg_manager.AsyncSession() as db:
            await db.delete(await db.get(User, user_id))
            await db.delete(await db.get(Department, department_id))
            await db.commit()
        await pg_manager.async_engine.dispose()
