from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import shutil
import textwrap
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic

from PIL import Image, ImageDraw, ImageFont

XHS_LOGIN_URL = "https://creator.xiaohongshu.com/login"
XHS_HOME_URL = "https://creator.xiaohongshu.com/new/home"
XHS_PUBLISH_NOTE_URL = "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image"
XHS_SUCCESS_URL_PATTERN = "**/publish/success?**"
XHS_DRAFT_ENTRY_PATTERN = re.compile(r"^草稿箱\s*(?:[（(]\d+[)）])?$")
XHS_DRAFT_TAB_PATTERN = re.compile(r"视频笔记\s*[（(]\d+[)）]")
LOGIN_BOX_SELECTOR = "div[class*='login-box']"
QR_IMAGE_SELECTOR = "img.css-1lhmg90"
QR_SWITCH_SELECTOR = "img.css-wemwzq"
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class XiaohongshuRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class XiaohongshuRuntime:
    def __init__(self, *, root: str | Path | None = None, headless: bool = True):
        self.root = Path(root or os.getenv("XHS_RUNTIME_ROOT", "/app/saves/xiaohongshu")).resolve()
        self.headless = headless

    def account_dir(self, owner_uid: str, account_id: str) -> Path:
        if not owner_uid or not SAFE_ID_PATTERN.fullmatch(account_id):
            raise XiaohongshuRuntimeError("XHS_INVALID_ACCOUNT_PATH", "账号运行目录标识无效")
        owner_namespace = hashlib.sha256(owner_uid.encode()).hexdigest()
        target = (self.root / owner_namespace / account_id).resolve()
        if not target.is_relative_to(self.root):
            raise XiaohongshuRuntimeError("XHS_INVALID_ACCOUNT_PATH", "账号运行目录越界")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def remove_account_dir(self, owner_uid: str, account_id: str) -> None:
        target = self.account_dir(owner_uid, account_id)
        if target.exists():
            shutil.rmtree(target)

    async def _launch_context(self, playwright, owner_uid: str, account_id: str):
        profile_dir = self.account_dir(owner_uid, account_id) / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            return await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                viewport={"width": 1440, "height": 1000},
                locale="zh-CN",
            )
        except Exception as exc:
            raise XiaohongshuRuntimeError("XHS_BROWSER_START_FAILED", "浏览器启动失败，请稍后重试") from exc

    @staticmethod
    async def _is_logged_in(page) -> bool:
        if page.url.startswith(XHS_LOGIN_URL):
            return False
        login_box = page.locator(LOGIN_BOX_SELECTOR).first
        if not await login_box.count():
            return True
        try:
            return not await login_box.is_visible()
        except Exception:
            return True

    @staticmethod
    async def _profile(page) -> dict[str, str]:
        try:
            body = await page.locator("body").inner_text(timeout=15000)
        except Exception:
            return {"nickname": "", "account_id": ""}
        lines = [" ".join(item.split()).strip() for item in body.splitlines() if item.strip()]
        account_id = ""
        for line in lines:
            if "小红书账号:" in line:
                account_id = line.split("小红书账号:", 1)[1].strip()
                break
        ignored = {"创作服务平台", "发布笔记", "首页", "笔记管理", "数据看板"}
        nickname = ""
        for marker in ("创作服务平台", "发布笔记"):
            if marker in lines:
                index = lines.index(marker)
                candidates = (
                    lines[index + 1 : index + 3] if marker == "创作服务平台" else lines[max(0, index - 2) : index]
                )
                nickname = next(
                    (item for item in candidates if item not in ignored and not item.isdigit() and len(item) <= 32),
                    "",
                )
                if nickname:
                    break
        return {"nickname": nickname, "account_id": account_id}

    async def open_drafts(self, page) -> None:
        """Open the creator-platform draft drawer without exposing arbitrary navigation."""
        from patchright.async_api import TimeoutError as BrowserTimeoutError

        if not await self._is_logged_in(page):
            raise XiaohongshuRuntimeError("XHS_LOGIN_REQUIRED", "账号登录已失效，请重新扫码")

        async def draft_box_is_open() -> bool:
            indicator = page.get_by_text(XHS_DRAFT_TAB_PATTERN).first
            try:
                return bool(await indicator.count()) and await indicator.is_visible()
            except Exception:
                return False

        if await draft_box_is_open():
            return

        await page.goto(XHS_PUBLISH_NOTE_URL, wait_until="domcontentloaded", timeout=60000)
        draft_entry = page.get_by_text(XHS_DRAFT_ENTRY_PATTERN).first
        try:
            await draft_entry.wait_for(state="visible", timeout=15000)
            await draft_entry.click()
            await page.get_by_text(XHS_DRAFT_TAB_PATTERN).first.wait_for(state="visible", timeout=15000)
        except BrowserTimeoutError as exc:
            raise XiaohongshuRuntimeError("XHS_DRAFTS_OPEN_UNCONFIRMED", "未能自动打开小红书草稿箱") from exc

    async def check_status(self, owner_uid: str, account_id: str) -> dict[str, str | bool]:
        from patchright.async_api import async_playwright

        async with async_playwright() as playwright:
            context = await self._launch_context(playwright, owner_uid, account_id)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2500)
                logged_in = await self._is_logged_in(page)
                profile = await self._profile(page) if logged_in else {"nickname": "", "account_id": ""}
                return {"logged_in": logged_in, **profile}
            finally:
                await context.close()

    async def login(
        self,
        owner_uid: str,
        account_id: str,
        *,
        qr_callback: Callable[[str], Awaitable[None]],
        timeout_seconds: int = 150,
    ) -> dict[str, str | bool]:
        from patchright.async_api import async_playwright

        async with async_playwright() as playwright:
            context = await self._launch_context(playwright, owner_uid, account_id)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(XHS_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                if await self._is_logged_in(page):
                    await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=60000)
                    return {"logged_in": True, **(await self._profile(page))}

                login_box = page.locator(LOGIN_BOX_SELECTOR).first
                await login_box.wait_for(state="visible", timeout=30000)
                qr = login_box.locator(QR_IMAGE_SELECTOR).first
                if not await qr.count():
                    switch = login_box.locator(QR_SWITCH_SELECTOR).first
                    await switch.wait_for(state="visible", timeout=10000)
                    await switch.click()
                    qr = login_box.locator(QR_IMAGE_SELECTOR).first
                await qr.wait_for(state="visible", timeout=15000)
                src = await qr.get_attribute("src")
                if src and src.startswith("data:image/"):
                    qr_data = src
                else:
                    image_bytes = await qr.screenshot(type="png")
                    qr_data = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
                await qr_callback(qr_data)

                deadline = monotonic() + timeout_seconds
                while monotonic() < deadline:
                    if await self._is_logged_in(page):
                        state_path = self.account_dir(owner_uid, account_id) / "storage-state.json"
                        await context.storage_state(path=str(state_path))
                        await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=60000)
                        await page.wait_for_timeout(2000)
                        return {"logged_in": True, **(await self._profile(page))}
                    await asyncio.sleep(1)
                raise XiaohongshuRuntimeError("XHS_LOGIN_EXPIRED", "二维码已过期，请重新扫码")
            finally:
                await context.close()

    def render_cover(self, owner_uid: str, account_id: str, job_id: str, title: str, topics: list[str]) -> Path:
        output_dir = self.account_dir(owner_uid, account_id) / "jobs" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "cover.png"
        image = Image.new("RGB", (1080, 1440), "#F6F7FB")
        draw = ImageDraw.Draw(image)
        font_candidates = (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
        )
        font_path = next((item for item in font_candidates if Path(item).is_file()), None)
        title_font = ImageFont.truetype(font_path, 82) if font_path else ImageFont.load_default()
        meta_font = ImageFont.truetype(font_path, 34) if font_path else ImageFont.load_default()
        draw.rounded_rectangle((72, 90, 1008, 1350), radius=42, fill="#FFFFFF", outline="#E7E9F0", width=3)
        draw.rounded_rectangle((116, 144, 374, 206), radius=31, fill="#FF2442")
        draw.text((154, 154), "Yuxi 创作", fill="#FFFFFF", font=meta_font)
        lines = textwrap.wrap(title, width=10)[:5]
        draw.multiline_text((118, 356), "\n".join(lines), fill="#16181D", font=title_font, spacing=32)
        topic_text = "  ".join(f"#{item}" for item in topics[:4]) or "由 Yuxi 内容工作台生成"
        draw.text((118, 1210), topic_text, fill="#6B7280", font=meta_font)
        image.save(output_path, format="PNG", optimize=True)
        return output_path

    async def distribute_on_page(
        self,
        page,
        owner_uid: str,
        account_id: str,
        job_id: str,
        *,
        title: str,
        body: str,
        topics: list[str],
        mode: str,
        cover_bytes: bytes | None = None,
    ) -> dict[str, str]:
        from patchright.async_api import TimeoutError as BrowserTimeoutError

        if cover_bytes is None:
            cover_path = self.render_cover(owner_uid, account_id, job_id, title, topics)
        else:
            output_dir = self.account_dir(owner_uid, account_id) / "jobs" / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            cover_path = output_dir / "cover.png"
            cover_path.write_bytes(cover_bytes)
        output_dir = cover_path.parent
        screenshot_path = output_dir / "result.png"
        try:
            await page.goto(XHS_PUBLISH_NOTE_URL, wait_until="domcontentloaded", timeout=60000)
            if not await self._is_logged_in(page):
                raise XiaohongshuRuntimeError("XHS_LOGIN_REQUIRED", "账号登录已失效，请重新扫码")

            upload = page.locator('input[type="file"][accept*="image"]').first
            if not await upload.count():
                upload = page.locator("div[class^='upload-content'] input.upload-input").first
            await upload.wait_for(state="attached", timeout=30000)
            await upload.set_input_files([str(cover_path)])

            title_input = page.locator('input[placeholder*="填写标题"]').first
            try:
                await title_input.wait_for(state="visible", timeout=60000)
            except BrowserTimeoutError as exc:
                raise XiaohongshuRuntimeError("XHS_UPLOAD_TIMEOUT", "图片上传超时") from exc
            await title_input.fill(title)

            editor = page.locator('p[data-placeholder*="输入正文描述"]').first
            await editor.wait_for(state="visible", timeout=15000)
            await editor.click()
            await page.keyboard.press("Control+KeyA")
            await page.keyboard.press("Delete")
            await page.keyboard.insert_text(body)
            for topic in topics:
                await page.keyboard.press("End")
                await page.keyboard.insert_text(f" #{topic}")
                suggestion = page.locator("#creator-editor-topic-container .item").first
                try:
                    await suggestion.wait_for(state="visible", timeout=3000)
                    await suggestion.click()
                except BrowserTimeoutError:
                    await page.keyboard.press("Space")

            if mode == "draft":
                clicked = False
                for label in ("保存草稿", "存草稿", "暂存草稿", "暂存离开", "保存到草稿箱"):
                    button = page.get_by_role("button", name=label, exact=False).first
                    if await button.count() and await button.is_visible():
                        await button.click()
                        clicked = True
                        break
                if not clicked:
                    raise XiaohongshuRuntimeError("XHS_DRAFT_BUTTON_MISSING", "未找到保存草稿按钮")
                try:
                    await page.get_by_text(re.compile("草稿.*(成功|已保存)|保存成功")).first.wait_for(
                        state="visible", timeout=15000
                    )
                except BrowserTimeoutError as exc:
                    raise XiaohongshuRuntimeError("XHS_DRAFT_UNCONFIRMED", "已点击保存，但未收到草稿成功确认") from exc
                note_url = ""
            else:
                button = page.get_by_role("button", name="发布", exact=True).first
                await button.wait_for(state="visible", timeout=15000)
                await button.click()
                try:
                    await page.wait_for_url(XHS_SUCCESS_URL_PATTERN, timeout=60000)
                except BrowserTimeoutError as exc:
                    raise XiaohongshuRuntimeError("XHS_PUBLISH_UNCONFIRMED", "未收到发布成功确认") from exc
                note_url = page.url

            await page.screenshot(path=str(screenshot_path), full_page=True)
            return {"note_url": note_url, "screenshot_path": str(screenshot_path)}
        except XiaohongshuRuntimeError:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception:
                pass
            raise XiaohongshuRuntimeError("XHS_BROWSER_ERROR", "小红书页面操作失败，请稍后重试") from exc

    async def distribute(
        self,
        owner_uid: str,
        account_id: str,
        job_id: str,
        *,
        title: str,
        body: str,
        topics: list[str],
        mode: str,
        cover_bytes: bytes | None = None,
    ) -> dict[str, str]:
        from patchright.async_api import async_playwright

        async with async_playwright() as playwright:
            context = await self._launch_context(playwright, owner_uid, account_id)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                return await self.distribute_on_page(
                    page,
                    owner_uid,
                    account_id,
                    job_id,
                    title=title,
                    body=body,
                    topics=topics,
                    mode=mode,
                    cover_bytes=cover_bytes,
                )
            finally:
                await context.close()
