#!/usr/bin/env python3
"""Local playwright against public test URL to reproduce 内容生产 nav."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("NAV_BASE", "http://124.232.148.28:8080")
USER = os.environ.get("NAV_USER", "admin")
PASSWORD = os.environ.get("NAV_PASSWORD", "")
OUT = Path(__file__).resolve().with_suffix(".out.json")


def main() -> int:
    # Prefer env; else try reading .env for admin password if present
    password = PASSWORD
    if not password:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("ADMIN_PASSWORD="):
                    password = line.split("=", 1)[1].strip().strip('"').strip("'")
                if line.startswith("ADMIN_USERNAME="):
                    global USER
                    # noqa - keep simple
                    pass
    page_errors = []
    console_errors = []
    failed = []
    bad = []

    with sync_playwright() as p:
        browser = None
        for launcher in (
            lambda: p.chromium.launch(headless=True),
            lambda: p.chromium.launch(headless=True, channel="msedge"),
            lambda: p.chromium.launch(headless=True, channel="chrome"),
        ):
            try:
                browser = launcher()
                break
            except Exception as e:
                print("launch_fail", e, file=sys.stderr)
        if browser is None:
            print("NO_BROWSER", file=sys.stderr)
            return 2

        page = browser.new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on(
            "requestfailed",
            lambda r: failed.append(
                {"url": r.url, "err": r.failure if isinstance(r.failure, str) else getattr(r.failure, "error_text", str(r.failure))}
            ),
        )

        def on_resp(r):
            u = r.url
            if r.status >= 400 and ("/src/" in u or "/api/content" in u or "ContentStudio" in u):
                bad.append({"url": u, "status": r.status})

        page.on("response", on_resp)

        page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=90000)
        # login form
        page.fill('input[type="text"], input[placeholder*="账号"], input[placeholder*="用户"]', USER)
        # try common selectors
        for sel in ['input[type="password"]', 'input[placeholder*="密码"]']:
            if page.locator(sel).count():
                page.fill(sel, password or "admin")
                break
        for sel in ['button:has-text("登录")', 'button[type="submit"]']:
            if page.locator(sel).count():
                page.click(sel)
                break
        page.wait_for_timeout(4000)

        # If still on login, try token mint via exposed API password grant if any
        if "/login" in page.url:
            # fallback: use stored token from env
            token = os.environ.get("NAV_TOKEN", "")
            if token:
                page.evaluate("(t)=>localStorage.setItem('user_token', t)", token)
            else:
                result = {
                    "error": "login_failed",
                    "href": page.url,
                    "text": page.inner_text("body")[:500],
                    "pageErrors": page_errors[:20],
                    "consoleErrors": console_errors[:30],
                }
                OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
                browser.close()
                return 1

        page.goto(f"{BASE}/model-manage/employees", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        before = page.evaluate(
            """() => ({
            href: location.href,
            hasEmployees: (document.body.innerText||'').includes('员工编码'),
            nav: [...document.querySelectorAll('a.nav-item')].map(el=>(el.textContent||'').trim()).slice(0,15)
          })"""
        )
        clicked = page.evaluate(
            """() => {
            const items=[...document.querySelectorAll('a.nav-item')];
            const t=items.find(el=>(el.textContent||'').includes('内容生产'));
            if(!t) return {ok:false, texts:items.map(i=>(i.textContent||'').trim()).slice(0,20)};
            t.click();
            return {ok:true, href:t.getAttribute('href')};
          }"""
        )
        for _ in range(40):
            page.wait_for_timeout(500)
            if "/content/" in page.url:
                break
        after = page.evaluate(
            """() => ({
            href: location.href,
            hasEmployees: (document.body.innerText||'').includes('员工编码'),
            text: (document.body.innerText||'').slice(0,1000),
            activeNav: [...document.querySelectorAll('.nav-item.active')].map(el=>(el.textContent||'').trim())
          })"""
        )
        page.goto(f"{BASE}/content/new", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)
        direct = page.evaluate(
            """() => ({
            href: location.href,
            hasEmployees: (document.body.innerText||'').includes('员工编码'),
            text: (document.body.innerText||'').slice(0,1000)
          })"""
        )
        result = {
            "before": before,
            "clicked": clicked,
            "after": after,
            "direct": direct,
            "pageErrors": page_errors[:30],
            "consoleErrors": console_errors[:50],
            "failed": failed[:30],
            "bad": bad[:30],
        }
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:8000])
        browser.close()
        return 0


if __name__ == "__main__":
    # fix accidental global comment
    raise SystemExit(main())
