#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("NAV_BASE", "http://124.232.148.28:8080")
OUT = Path(__file__).resolve().with_suffix(".out.json")
TOKEN_FILES = [
    Path(__file__).resolve().with_name("_auth_open_content_host.out.txt"),
    Path(__file__).resolve().with_name("_auth_open_content_new2.out.txt"),
    Path(__file__).resolve().with_name("_auth_open_content_docker_pw.out.txt"),
]


def load_token() -> str:
    token = os.environ.get("NAV_TOKEN", "").strip()
    if token:
        return token
    for path in TOKEN_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^TOKEN\s+(\S+)", text, re.M)
        if m:
            return m.group(1).strip()
    return ""


def main() -> int:
    token = load_token()
    if not token:
        print("NO_TOKEN", file=sys.stderr)
        return 1

    page_errors: list[str] = []
    console_errors: list[str] = []
    failed: list[dict] = []
    bad: list[dict] = []

    with sync_playwright() as p:
        last_err = None
        browser = None
        for kwargs in (
            {"headless": True, "channel": "msedge"},
            {"headless": True, "channel": "chrome"},
            {"headless": True},
        ):
            try:
                browser = p.chromium.launch(**kwargs)
                print("launched", kwargs)
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                print("launch_fail", kwargs, e, file=sys.stderr)
        if browser is None:
            print("NO_BROWSER", last_err, file=sys.stderr)
            return 2

        page = browser.new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on(
            "console",
            lambda m: console_errors.append(m.text) if m.type == "error" else None,
        )
        page.on(
            "requestfailed",
            lambda r: failed.append({"url": r.url, "err": str(r.failure)}),
        )

        def on_resp(r):
            u = r.url
            if r.status >= 400 and (
                "/src/" in u or "/api/content" in u or "ContentStudio" in u
            ):
                bad.append({"url": u, "status": r.status})

        page.on("response", on_resp)

        page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=90000)
        page.evaluate("(t) => localStorage.setItem('user_token', t)", token)
        page.goto(
            f"{BASE}/model-manage/employees",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        page.wait_for_timeout(3000)
        before = page.evaluate(
            """() => ({
              href: location.href,
              hasEmployees: (document.body.innerText || '').includes('员工编码'),
              nav: [...document.querySelectorAll('a.nav-item')].map(el => (el.textContent || '').trim()).slice(0, 15)
            })"""
        )
        clicked = page.evaluate(
            """() => {
              const items = [...document.querySelectorAll('a.nav-item')];
              const t = items.find(el => (el.textContent || '').includes('内容生产'));
              if (!t) return { ok: false, texts: items.map(i => (i.textContent || '').trim()).slice(0, 20) };
              t.click();
              return { ok: true, href: t.getAttribute('href') };
            }"""
        )
        for _ in range(40):
            page.wait_for_timeout(500)
            if "/content/" in page.url:
                break
        after = page.evaluate(
            """() => ({
              href: location.href,
              hasEmployees: (document.body.innerText || '').includes('员工编码'),
              text: (document.body.innerText || '').slice(0, 1000),
              activeNav: [...document.querySelectorAll('.nav-item.active')].map(el => (el.textContent || '').trim())
            })"""
        )
        page.goto(f"{BASE}/content/new", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)
        direct = page.evaluate(
            """() => ({
              href: location.href,
              hasEmployees: (document.body.innerText || '').includes('员工编码'),
              text: (document.body.innerText || '').slice(0, 1000)
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
        summary = {
            "before_href": before.get("href"),
            "clicked": clicked,
            "after_href": after.get("href"),
            "after_hasEmployees": after.get("hasEmployees"),
            "direct_href": direct.get("href"),
            "direct_hasEmployees": direct.get("hasEmployees"),
            "direct_text_head": (direct.get("text") or "")[:200],
            "pageErrors": page_errors[:10],
            "consoleErrors": console_errors[:20],
            "bad": bad[:20],
            "failed_head": failed[:10],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        browser.close()
        ok = (
            "/content/" in str(after.get("href") or "")
            or (
                "/content/" in str(direct.get("href") or "")
                and not direct.get("hasEmployees")
                and bool((direct.get("text") or "").strip())
            )
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
