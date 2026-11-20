#!/usr/bin/env python3
"""Reproduce nav using host chromium + playwright if available."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

TOKEN_SCRIPT = r'''
import os, asyncio, asyncpg
from yuxi.utils.auth_utils import AuthUtils
async def main():
    url=(os.environ.get('POSTGRES_URL') or '').replace('postgresql+asyncpg://','postgresql://')
    conn=await asyncpg.connect(url)
    try:
        rows=await conn.fetch("select id,username,role from users where role ilike '%admin%' order by id asc limit 5")
        if not rows:
            rows=await conn.fetch('select id,username,role from users order by id asc limit 1')
        print('user', dict(rows[0]))
        uid=rows[0]['id']
    finally:
        await conn.close()
    print('TOKEN', AuthUtils.create_access_token({'sub': str(uid)}))
asyncio.run(main())
'''


def run(c, cmd, timeout=240):
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
        with sftp.file("/tmp/_mint_token.py", "w") as f:
            f.write(TOKEN_SCRIPT)
        sftp.close()
        token_out = run(
            c,
            "docker cp /tmp/_mint_token.py api-dev:/tmp/_mint_token.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_mint_token.py",
            timeout=60,
        )
        token = ""
        for line in token_out.splitlines():
            if line.startswith("TOKEN "):
                token = line[6:].strip()
        if not token:
            OUT.write_text(token_out, encoding="utf-8")
            print(token_out)
            return 1

        # Discover chrome/chromium and python playwright on host
        discover = run(
            c,
            "echo '=== bins ==='; "
            "command -v google-chrome || true; command -v chromium || true; "
            "command -v chromium-browser || true; command -v google-chrome-stable || true; "
            "ls /usr/bin/chrom* 2>/dev/null || true; "
            "python3 -c 'import playwright; print(\"pw\", playwright.__version__)' 2>/dev/null || echo 'no-host-pw'; "
            "pip3 show playwright 2>/dev/null | head -n 2 || true",
            timeout=30,
        )

        py_script = f'''
import asyncio, json
from playwright.async_api import async_playwright

TOKEN = {token!r}
BASE = "http://127.0.0.1:8080"

async def main():
    page_errors=[]; console_errors=[]; failed=[]; bad=[]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        page = await browser.new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type=="error" else None)
        page.on("requestfailed", lambda r: failed.append({{"url": r.url, "err": (r.failure.error_text if r.failure else None)}}))
        def on_resp(r):
            u=r.url
            if r.status>=400 and ("/src/" in u or "/api/content" in u or "ContentStudio" in u):
                bad.append({{"url": u, "status": r.status}})
        page.on("response", on_resp)

        await page.goto(BASE+"/login", wait_until="domcontentloaded", timeout=60000)
        await page.evaluate("(t)=>localStorage.setItem('user_token', t)", TOKEN)
        await page.goto(BASE+"/model-manage/employees", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        before = await page.evaluate("""() => ({{
          href: location.href,
          hasEmployees: (document.body.innerText||'').includes('员工编码'),
          nav: [...document.querySelectorAll('a.nav-item')].map(el=>(el.textContent||'').trim()).slice(0,15)
        }})""")
        clicked = await page.evaluate("""() => {{
          const items=[...document.querySelectorAll('a.nav-item')];
          const t=items.find(el=>(el.textContent||'').includes('内容生产'));
          if(!t) return {{ok:false, texts:items.map(i=>(i.textContent||'').trim()).slice(0,20)}};
          t.click();
          return {{ok:true, href:t.getAttribute('href')}};
        }}""")
        for _ in range(30):
            await page.wait_for_timeout(500)
            if "/content/" in page.url:
                break
        after = await page.evaluate("""() => ({{
          href: location.href,
          hasEmployees: (document.body.innerText||'').includes('员工编码'),
          text: (document.body.innerText||'').slice(0,900),
          activeNav: [...document.querySelectorAll('.nav-item.active')].map(el=>(el.textContent||'').trim())
        }})""")
        await page.goto(BASE+"/content/new", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(5000)
        direct = await page.evaluate("""() => ({{
          href: location.href,
          hasEmployees: (document.body.innerText||'').includes('员工编码'),
          text: (document.body.innerText||'').slice(0,900)
        }})""")
        print(json.dumps({{
          "before": before,
          "clicked": clicked,
          "after": after,
          "direct": direct,
          "pageErrors": page_errors[:25],
          "consoleErrors": console_errors[:40],
          "failed": failed[:30],
          "bad": bad[:30],
        }}, ensure_ascii=False, indent=2))
        await browser.close()

asyncio.run(main())
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_nav_host_pw.py", "w") as f:
            f.write(py_script)
        sftp.close()

        # Ensure playwright+chromium on host quickly
        setup = run(
            c,
            "python3 -c 'import playwright' 2>/dev/null || pip3 install -q playwright==1.49.0; "
            "python3 -m playwright install chromium 2>&1 | tail -n 20",
            timeout=220,
        )
        out = run(c, "timeout 150 python3 /tmp/_nav_host_pw.py", timeout=180)
        text = "\n".join([token_out, "=== discover ===", discover, "=== setup ===", setup, "=== result ===", out])
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:10000])
        return 0 if '"after"' in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
