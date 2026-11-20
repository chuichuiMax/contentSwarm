#!/usr/bin/env python3
"""Use playwright inside web/api host to open content/new and capture console errors."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE_SCRIPT = r'''
import asyncio, json, os, urllib.request, urllib.error, asyncpg
from yuxi.utils.auth_utils import AuthUtils

async def admin():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("select id, username, role from users order by id asc limit 30")
        for r in rows:
            role = str(r["role"] or "").lower()
            name = str(r["username"] or "").lower()
            if role in {"admin", "superadmin"} or name in {"admin", "superadmin"}:
                return dict(r)
        return dict(rows[0])
    finally:
        await conn.close()

user = asyncio.run(admin())
token = AuthUtils.create_access_token({"sub": str(user["id"])})
print("user", user["username"], user["role"])

try:
    from playwright.async_api import async_playwright
except Exception as e:
    print("NO_PLAYWRIGHT", e)
    raise SystemExit(2)

async def main():
    errors = []
    page_errors = []
    failed = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        page.on("console", lambda msg: errors.append({"type": msg.type, "text": msg.text}) if msg.type in {"error", "warning"} else None)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("requestfailed", lambda req: failed.append({"url": req.url, "err": req.failure}))
        # seed auth like the app does
        await page.goto("http://web-dev:5173/login", wait_until="domcontentloaded", timeout=60000)
        await page.evaluate(
            """(token) => {
              localStorage.setItem('token', token);
              localStorage.setItem('access_token', token);
            }""",
            token,
        )
        # also try cookie style if app uses it
        await page.goto("http://web-dev:5173/content/new", wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(3000)
        info = await page.evaluate("""() => ({
          href: location.href,
          title: document.title,
          text: (document.body && document.body.innerText || '').slice(0, 500),
          hasStudio: !!document.querySelector('.content-studio, .studio, [class*=content]'),
        })""")
        print(json.dumps({
            "nav": info,
            "page_errors": page_errors[:20],
            "console": errors[:40],
            "failed": failed[:20],
        }, ensure_ascii=False, indent=2))
        await browser.close()

asyncio.run(main())
'''


def run(c, cmd, timeout=180):
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chan.recv_ready():
            chunks.append(chan.recv(65535))
        if chan.recv_stderr_ready():
            chunks.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready():
                chunks.append(chan.recv(65535))
            while chan.recv_stderr_ready():
                chunks.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    else:
        chan.close()
        raise TimeoutError(cmd[:120])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_browser_content_nav.py", "w") as f:
            f.write(REMOTE_SCRIPT)
        sftp.close()
        # Prefer api-dev python env; install playwright only if needed is too heavy.
        # Try patchright/playwright already present.
        out = run(
            c,
            "docker cp /tmp/_browser_content_nav.py api-dev:/tmp/_browser_content_nav.py && "
            "timeout 150 docker exec -w /app api-dev python /tmp/_browser_content_nav.py",
            timeout=170,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:5000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
