"""Verify public share image geometry in Chromium with controlled snapshot data."""

import io
from types import SimpleNamespace

import pytest
from patchright.async_api import async_playwright
from PIL import Image

from yuxi.services.material_library_service import render_public_material_share_page
from yuxi.services.material_share_images import make_share_display_webp


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("viewport_width", [375, 1024], ids=["mobile", "desktop"])
@pytest.mark.parametrize("image_size", [(600, 800), (1600, 800), (600, 600)], ids=["portrait", "landscape", "square"])
async def test_public_share_cover_preserves_image_ratio(viewport_width, image_size):
    share = SimpleNamespace(
        token="ratio-test",
        title="案例分享",
        building_name="测试楼盘",
        area="120",
        design_style="雅致现代",
        sharer_name="测试员工",
        sharer_phone="19900000001",
    )
    base_url = "https://share.example.test"
    html = render_public_material_share_page(share, [SimpleNamespace(display_order=1)], base_url)
    source = io.BytesIO()
    Image.new("RGB", image_size, "blue").save(source, format="PNG")
    image = make_share_display_webp(source.getvalue())

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": viewport_width, "height": 900})
            await page.route(
                f"{base_url}/share/case/ratio-test",
                lambda route: route.fulfill(content_type="text/html", body=html),
            )
            await page.route(
                f"{base_url}/api/material-library/shares/ratio-test/images/1.webp",
                lambda route: route.fulfill(content_type="image/webp", body=image),
            )
            await page.goto(f"{base_url}/share/case/ratio-test")
            await page.locator(".share-hero img").evaluate("img => img.decode()")
            geometry = await page.locator(".share-hero img").evaluate(
                """img => {
                    const image = img.getBoundingClientRect();
                    const hero = img.parentElement.getBoundingClientRect();
                    return {
                        imageWidth: image.width, imageHeight: image.height,
                        heroWidth: hero.width, heroHeight: hero.height,
                        naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight
                    };
                }"""
            )

            assert geometry["imageWidth"] == pytest.approx(min(viewport_width, 720), abs=1)
            expected_height = geometry["imageWidth"] * geometry["naturalHeight"] / geometry["naturalWidth"]
            assert geometry["imageHeight"] == pytest.approx(expected_height, abs=1)
            assert geometry["heroHeight"] == pytest.approx(expected_height, abs=1)
            assert geometry["heroWidth"] == pytest.approx(geometry["imageWidth"], abs=1)
            assert await page.locator(".project-info-card").is_visible()
            assert await page.locator('a[href="tel:19900000001"]').count() == 1
            assert await page.locator('meta[property="og:image:width"]').get_attribute("content") == "500"
            assert await page.locator('meta[property="og:image:height"]').get_attribute("content") == "400"
        finally:
            await browser.close()
