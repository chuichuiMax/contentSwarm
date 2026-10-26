"""Check login first-screen layout with native element defaults; device QA remains separate."""

import base64
import os
import re
from pathlib import Path

import pytest
from patchright.async_api import async_playwright, expect

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.mark.parametrize(
    "width,height,bottom_inset",
    [(375, 603, 0), (390, 651, 34), (390, 663, 34), (390, 701, 34), (390, 756, 34), (414, 808, 34)],
)
async def test_mp_login_controls_and_notice_fit_first_screen(tmp_path, width, height, bottom_inset):
    source_root = Path(os.environ["MP_SOURCE_DIR"])
    source = (source_root / "pages/login/login.vue").read_text(encoding="utf-8")
    template = source.split('<view v-if="helpOpen"', 1)[0].replace("<template>", "") + "</view>"
    template = re.sub(r'<button v-if="workId[^>]*>[\s\S]*?</button>', "", template)
    template = re.sub(r'<text v-if="wechatError"[^>]*>[\s\S]*?</text>', "", template)
    template = re.sub(r"\{\{[\s\S]*?\}\}", "输入工号后自动带出姓名和手机号", template)
    # HTML buttons size to content; WeChat buttons follow block/flex sizing.
    template = template.replace("<button", '<div role="button"').replace("</button>", "</div>")

    def image_tag(match):
        tag = match[0].replace("<image", "<img").replace("/>", ">")
        asset = re.search(r'src="([^"]+)"', tag)[1]
        mime = "image/svg+xml" if asset.endswith(".svg") else "image/png"
        encoded = base64.b64encode((source_root / asset.lstrip("/")).read_bytes()).decode()
        return tag.replace(asset, f"data:{mime};base64,{encoded}")

    template = re.sub(r"<image\b[^>]*/>", image_tag, template)
    styles = re.search(r"<style scoped>([\s\S]*?)</style>", source)[1]
    styles = styles.replace("env(safe-area-inset-bottom)", f"{bottom_inset}px")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.set_content(
                '<meta charset="utf-8"><style>body{margin:0;font-family:Arial,sans-serif}'
                "view{display:block}input{padding:0;border:0;background:transparent}"
                '[role="button"]{display:block;margin-left:auto;margin-right:auto;padding:0 14px;border:0;'
                "box-sizing:border-box;text-align:center;overflow:hidden}"
                "img[mode=aspectFit]{object-fit:contain}img[mode=aspectFill]{object-fit:cover}</style>"
                f"<style>{styles}</style>{template}"
            )
            for selector in (".logo", ".title", ".field-input", ".login-button", ".action-link", ".notice"):
                for element in await page.locator(selector).all():
                    await expect(element).to_be_in_viewport(ratio=1)
            notice = await page.locator(".notice").bounding_box()
            assert notice["y"] + notice["height"] + bottom_inset <= height
            if height >= 700:
                # Available room belongs between controls as well as below the form.
                assert height - bottom_inset - notice["y"] - notice["height"] <= height * 0.1
            assert await page.evaluate("document.documentElement.scrollHeight <= innerHeight")
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert await page.locator(".field-input").first.evaluate("el => el.clientHeight >= 52")
            assert await page.locator(".login-button").first.evaluate("el => el.clientHeight >= 50")
            await page.screenshot(path=str(tmp_path / f"login-{width}-{height}.png"))
            # A short keyboard viewport may scroll; the last action must stay reachable.
            await page.set_viewport_size({"width": width, "height": 340})
            await page.locator(".notice").scroll_into_view_if_needed()
            await expect(page.locator(".notice")).to_be_in_viewport(ratio=1)
        finally:
            await browser.close()
