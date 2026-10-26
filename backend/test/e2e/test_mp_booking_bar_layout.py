"""Browser layout regression using the MP page markup/CSS; native WeChat still needs device QA."""

import os
import re
from pathlib import Path

import pytest
from patchright.async_api import async_playwright, expect

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


@pytest.mark.parametrize("width,height,bottom_inset", [(375, 812, 0), (375, 812, 34), (812, 375, 21)])
async def test_mp_booking_stays_visible_while_case_scrolls_and_sheet_covers_it(tmp_path, width, height, bottom_inset):
    source = (Path(os.environ["MP_SOURCE_DIR"]) / "pages/materials/shared-case.vue").read_text(encoding="utf-8")
    template = source.split("<script>", 1)[0]
    template = re.sub(r"</?template[^>]*>", "", template)
    template = re.sub(
        r"<case-share-content\b[^>]*?/>",
        '<div id="case-images" style="height:2400px;background:#ddd">案例图片</div>',
        template,
        flags=re.S,
    )
    styles = re.search(r"<style scoped>([\s\S]*?)</style>", source)[1]
    # Resolve the native safe-area input explicitly; Chromium in CI has no WeChat safe-area provider.
    styles = styles.replace("env(safe-area-inset-bottom)", f"{bottom_inset}px")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": width, "height": height})
            # Supply only native element defaults; positioning comes from the actual page CSS.
            await page.set_content(
                "<style>body{margin:0}view,scroll-view{display:block}scroll-view{overflow-y:auto}"
                "button{padding:0;border:0;box-sizing:border-box}.state-view{display:none}</style>"
                f"<style>{styles}</style>{template}"
            )
            await page.locator(".state-view").evaluate_all("els => els.forEach(el => el.remove())")
            await page.locator(".booking-mask").evaluate("el => el.style.display = 'none'")
            bar = page.locator(".booking-entry")
            scroll = page.locator(".case-scroll")
            initial = await bar.bounding_box()
            assert initial and initial["y"] >= 0 and initial["y"] + initial["height"] == pytest.approx(height, abs=1)
            spacer = await page.locator(".booking-spacer").bounding_box()
            assert spacer["height"] == 88 + bottom_inset
            await expect(bar.locator("button")).to_be_in_viewport(ratio=1)
            for fraction in (0.5, 1):
                await scroll.evaluate("(el, fraction) => el.scrollTop = el.scrollHeight * fraction", fraction)
                current = await bar.bounding_box()
                assert current["y"] == pytest.approx(initial["y"], abs=1)
                await expect(bar.locator("button")).to_be_in_viewport(ratio=1)
            last_image = await page.locator("#case-images").bounding_box()
            assert last_image["y"] + last_image["height"] <= initial["y"] + 1
            assert await page.evaluate("document.documentElement.scrollHeight <= innerHeight")
            await page.screenshot(path=str(tmp_path / "mp-booking-bottom.png"))
            await page.locator(".booking-mask").evaluate("el => el.style.removeProperty('display')")
            assert await bar.evaluate(
                "el => { const rect = el.getBoundingClientRect(); "
                "return Boolean(document.elementFromPoint(rect.x + rect.width / 2, rect.y + 12)"
                "?.closest('.booking-mask')); }"
            )
            await page.locator(".booking-mask").evaluate("el => el.style.display = 'none'")
            await expect(bar.locator("button")).to_be_in_viewport(ratio=1)
        finally:
            await browser.close()
