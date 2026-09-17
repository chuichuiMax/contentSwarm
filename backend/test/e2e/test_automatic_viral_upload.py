"""真实上传 -> 解析 -> 自动识别 -> 单篇准备；合成知识库用后清理。"""

import asyncio
import os
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentViralArticleVersion, ContentViralFileJob
from yuxi.utils.auth_utils import AuthUtils


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("file_format", ["md", "xlsx"])
async def test_upload_opt_in_automatically_prepares_complete_articles(file_format):
    uid = os.environ.get("RULE_EDITOR_TEST_UID")
    if not uid:
        pytest.skip("需要配置专用测试管理员 UID")
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        headers = {"Authorization": "Bearer " + AuthUtils.create_access_token({"sub": str(user.id)})}
    await pg_manager.async_engine.dispose()
    kb_id = None
    async with httpx.AsyncClient(base_url="http://localhost:5050", timeout=60) as client:
        try:
            response = await client.post(
                "/api/knowledge/databases",
                headers=headers,
                json={
                    "database_name": "自动参考验收_" + uuid.uuid4().hex[:8],
                    "description": "隔离合成参考测试，不声称真实平台互动量",
                    "kb_type": "milvus",
                    "embedding_model_spec": "alibaba:text-embedding-v4",
                },
            )
            response.raise_for_status()
            data = response.json()
            kb_id = data.get("kb_id") or data.get("database", {}).get("kb_id")
            assert kb_id, data

            async def upload(name, text, selected):
                response = await client.post(
                    "/api/knowledge/files/upload",
                    params={"kb_id": kb_id},
                    headers=headers,
                    files={
                        "file": (name, text.encode() if isinstance(text, str) else text, "application/octet-stream")
                    },
                )
                response.raise_for_status()
                source = response.json()
                response = await client.post(
                    f"/api/knowledge/databases/{kb_id}/documents",
                    headers=headers,
                    json={
                        "items": [source["file_path"]],
                        "params": {
                            "content_type": "file",
                            "ocr_engine": "disable",
                            "auto_index": False,
                            "content_hashes": {source["file_path"]: source["content_hash"]},
                            "use_as_viral_reference": selected,
                        },
                    },
                )
                response.raise_for_status()
                task_id = response.json()["task_id"]
                for _ in range(60):
                    task = (await client.get(f"/api/tasks/{task_id}", headers=headers)).json()["task"]
                    if task["status"] not in {"pending", "running"}:
                        break
                    await asyncio.sleep(1)
                assert task["status"] == "success", task
                assert task["result"]["failed"] == 0, task
                return task["result"]["items"][0]["file_id"]

            ordinary = await upload("普通知识.md", "# 普通知识\n只作为知识资料，不选择参考用途。", False)
            article_text = (
                "| 文案名 | 笔记内容 |\n| --- | --- |\n"
                "| 厨房动线先从做饭习惯开始 | 厨房里总是来回走，可以先看看自己的做饭习惯。<br>"
                "把洗菜、备菜、炒菜按顺序安排，常用锅具就近收纳。<br>确定这些习惯后，再规划柜体位置。<br>"
                "你家厨房最不顺手的是哪一步？ |\n"
                "| 零基础英语先练开口 | 学英语总是不敢说，可以先从熟悉的工作场景开始。<br>"
                "选一句工作中常用的表达，理解意思后练习说出来。<br>再用自己的情境替换词语，逐步扩展表达。<br>"
                "你最想练习哪个工作场景？ |\n"
            )
            article_name = "两篇参考.md"
            if file_format == "xlsx":
                from io import BytesIO
                from openpyxl import Workbook

                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "参考文案"
                sheet.append(["文案名", "笔记内容"])
                for line in article_text.splitlines()[2:]:
                    title, body = [cell.strip() for cell in line.strip("|").split("|")]
                    sheet.append([title, body.replace("<br>", "\n")])
                stream = BytesIO()
                workbook.save(stream)
                article_text = stream.getvalue()
                article_name = "两篇参考.xlsx"
            articles = await upload(article_name, article_text, True)
            quote = await upload(
                "报价资料.md", "| 材料 | 单价 |\n| --- | --- |\n| 板材 | 200 元 |\n| 五金 | 100 元 |", True
            )
            for _ in range(160):
                jobs = (await client.get("/api/content/viral-file-jobs", headers=headers)).json()["items"]
                own = {job["file_id"]: job for job in jobs if job["kb_id"] == kb_id}
                assert ordinary not in own
                if articles in own and quote in own:
                    assert own[articles]["status"] != "failed", own[articles]
                    assert own[quote]["status"] != "failed", own[quote]
                    if own[articles]["ready_count"] == 2 and own[quote]["status"] == "needs_review":
                        break
                await asyncio.sleep(2)
            assert own[articles]["ready_count"] == 2, own
            assert own[articles]["status"] == "completed", own[articles]
            assert own[quote]["article_count"] == 0 and own[quote]["error_message"], own[quote]
            assets = (await client.get("/api/content/viral-assets", headers=headers)).json()["items"]
            assets = [asset for asset in assets if asset["file_id"] == articles]
            assert len(assets) == 2
            assert {asset["industry_slug"] for asset in assets} == {"decoration", "education"}
            for asset in assets:
                detail = (await client.get("/api/content/viral-assets/" + asset["id"], headers=headers)).json()["asset"]
                assert detail["preparation"]["reference_blueprint"]["content_block_sequence"]
                assert detail["preparation"]["blueprint_anchors"]
                assert "用户选择" in detail["source"]["viral_basis"]
                assert detail["source"]["body"].count("\n") >= 3, "单元格换行不能被合并"
            response = await client.post(
                "/api/content/viral-file-jobs", headers=headers, json={"kb_id": kb_id, "file_ids": [articles, articles]}
            )
            response.raise_for_status()
            assert len(response.json()["items"]) == 1
            assert response.json()["items"][0]["id"] == own[articles]["id"]
            assert response.json()["items"][0]["attempt"] == 1
            duplicate_issue = await client.post(
                "/api/content/viral-file-jobs", headers=headers, json={"kb_id": kb_id, "file_ids": [quote]}
            )
            assert duplicate_issue.json()["items"][0]["attempt"] == 1
            assert (await client.get("/api/content/viral-file-jobs")).status_code in {401, 403}
            assert (
                await client.post(
                    "/api/content/viral-file-jobs", headers=headers, json={"kb_id": kb_id, "file_ids": []}
                )
            ).status_code == 422
            if os.environ.get("REFERENCE_BROWSER_E2E") == "1":
                import json
                import socket
                from patchright.async_api import async_playwright, expect

                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}",
                        ],
                    )
                    try:
                        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                        token = headers["Authorization"].removeprefix("Bearer ")
                        await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
                        page = await context.new_page()
                        await page.goto(f"http://localhost:5173/knowledge/{kb_id}")
                        await page.get_by_role("button", name="上传", exact=True).click()
                        checkbox = page.get_by_role("checkbox", name="用于爆款仿写参考", exact=True)
                        await expect(checkbox).to_be_visible()
                        await expect(checkbox).not_to_be_checked()
                        await checkbox.check()
                        await expect(
                            page.get_by_text(
                                "解析完成后自动识别完整文章，准备参考卡和结构蓝图；可在创作规则库查看进度。"
                            )
                        ).to_be_visible()
                        await page.screenshot(path="/tmp/automatic-reference-upload.png", full_page=True)
                        await page.goto("http://localhost:5173/content/admin/rules")
                        await page.get_by_text("爆款参考资产", exact=True).click()
                        await page.get_by_role("button", name="从已有文件准备").click()
                        await expect(page.get_by_text("从已有文件准备参考", exact=True)).to_be_visible()
                        await expect(page.get_by_text("参考认定依据", exact=True)).to_have_count(0)
                        await expect(page.get_by_text("标题列名", exact=True)).to_have_count(0)
                        await page.get_by_role("button", name="取 消").click()
                        row = page.get_by_role("row").filter(has_text="厨房动线先从做饭习惯开始")
                        await row.get_by_role("button", name="原文与蓝图").click()
                        await expect(page.get_by_text("参考卡 · 判断是否适合本次创作")).to_be_visible()
                        await expect(page.get_by_text("结构蓝图 · 指导选中后的创作")).to_be_visible()
                        await expect(
                            page.locator(".ant-descriptions").get_by_text("正文信息块顺序", exact=True)
                        ).to_be_visible()
                        await expect(page.locator(".ant-drawer-content-wrapper")).to_be_in_viewport(ratio=1)
                        await page.screenshot(path="/tmp/automatic-reference-blueprint.png", full_page=True)
                    finally:
                        await browser.close()
        finally:
            if kb_id:
                async with pg_manager.AsyncSession() as db:
                    await db.execute(
                        delete(ContentViralArticleVersion).where(ContentViralArticleVersion.kb_id == kb_id)
                    )
                    await db.execute(delete(ContentViralFileJob).where(ContentViralFileJob.kb_id == kb_id))
                    await db.commit()
                await pg_manager.async_engine.dispose()
                response = await client.delete("/api/knowledge/databases/" + kb_id, headers=headers)
                assert response.status_code == 200, response.text
