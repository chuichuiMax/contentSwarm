"""行业筛选展示唯一行业，模板历史仍完整保留。"""

import json
import os
import socket
from urllib.parse import parse_qs, urlparse

import pytest
from patchright.async_api import async_playwright, expect


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_viral_filter_deduplicates_template_versions(e2e_client, e2e_headers):
    response = await e2e_client.get("/api/content/admin/industry-templates", headers=e2e_headers)
    assert response.status_code == 200
    templates = response.json()["items"]
    assert templates
    industries = {item["slug"]: item["name"] for item in templates}
    token = e2e_headers["Authorization"].removeprefix("Bearer ")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", f"--host-resolver-rules=MAP localhost {socket.gethostbyname('web')}"],
        )
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.add_init_script(f"localStorage.setItem('user_token', {json.dumps(token)})")
            page = await context.new_page()
            await page.goto(f"{os.getenv('TEST_WEB_URL', 'http://localhost:5173')}/content/admin/rules")
            await page.get_by_role("tab", name="爆款参考资产", exact=True).click()
            await page.locator(".viral-assets .asset-toolbar .ant-select").click()
            options = page.locator(".ant-select-dropdown:visible .ant-select-item-option-content")
            await expect(options).to_have_count(len(industries))
            assert set(await options.all_text_contents()) == set(industries.values())
            await page.screenshot(path="/tmp/yuxi-viral-industry-filter.png", full_page=True)
            slug, name = next(iter(industries.items()))
            async with page.expect_response(
                lambda result: (
                    urlparse(result.url).path == "/api/content/viral-assets"
                    and parse_qs(urlparse(result.url).query).get("industry_slug") == [slug]
                )
            ) as filtered:
                await options.filter(has_text=name).click()
            assert (await filtered.value).status == 200
            await page.get_by_role("tab", name="行业包", exact=False).click()
            history = page.locator("details.legacy-templates")
            await history.locator("summary").click()
            await expect(history.locator("article")).to_have_count(len(templates))
        finally:
            await browser.close()
