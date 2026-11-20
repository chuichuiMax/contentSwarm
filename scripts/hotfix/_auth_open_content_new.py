#!/usr/bin/env python3
"""Mint admin token and use node+playwright from npx if possible to open /content/new."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=300):
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


TOKEN_SCRIPT = r'''
import os, asyncio, asyncpg, json
from yuxi.utils.auth_utils import AuthUtils
async def main():
    url=(os.environ.get('POSTGRES_URL') or '').replace('postgresql+asyncpg://','postgresql://')
    conn=await asyncpg.connect(url)
    try:
        rows=await conn.fetch('select id,username,role from users order by id asc limit 30')
        uid=None
        for r in rows:
            if str(r['role']).lower() in {'admin','superadmin'} or str(r['username']).lower() in {'admin','superadmin'}:
                uid=r['id']; print('user', dict(r)); break
        if uid is None: uid=rows[0]['id']
    finally:
        await conn.close()
    print('TOKEN', AuthUtils.create_access_token({'sub': str(uid)}))
asyncio.run(main())
'''


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
            "timeout 30 docker exec -w /app api-dev python /tmp/_mint_token.py",
            timeout=45,
        )
        token = ""
        for line in token_out.splitlines():
            if line.startswith("TOKEN "):
                token = line[6:].strip()
        print(token_out.encode("ascii", "replace").decode("ascii"))
        if not token:
            OUT.write_text(token_out, encoding="utf-8")
            return 1

        # Use web-dev npx playwright for a one-shot check
        node_script = f'''
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch({{ headless: true, args: ['--no-sandbox'] }});
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const failed = [];
  page.on('pageerror', e => pageErrors.push(String(e)));
  page.on('console', m => {{ if (m.type() === 'error') consoleErrors.push(m.text()); }});
  page.on('requestfailed', r => failed.push({{ url: r.url(), err: r.failure() }}));
  await page.goto('http://127.0.0.1:5173/login', {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.evaluate(token => {{
    localStorage.setItem('token', token);
  }}, {token!r});
  await page.goto('http://127.0.0.1:5173/model-manage/employees', {{ waitUntil: 'networkidle', timeout: 90000 }});
  await page.wait_for_timeout(1000);
  // click 内容生产
  const clicked = await page.evaluate(() => {{
    const items = [...document.querySelectorAll('a.nav-item, .nav-item')];
    const target = items.find(el => (el.textContent || '').includes('内容生产'));
    if (!target) return {{ ok: false, reason: 'not found', texts: items.map(i => i.textContent.trim()).slice(0, 12) }};
    target.click();
    return {{ ok: true, hrefBefore: location.href }};
  }});
  await page.wait_for_timeout(4000);
  const after = await page.evaluate(() => ({{
    href: location.href,
    text: (document.body.innerText || '').slice(0, 600),
    activeNav: [...document.querySelectorAll('.nav-item.active')].map(el => el.textContent.trim()),
  }}));
  console.log(JSON.stringify({{ clicked, after, pageErrors, consoleErrors: consoleErrors.slice(0, 30), failed: failed.slice(0, 20) }}, null, 2));
  await browser.close();
}})().catch(e => {{ console.error(String(e)); process.exit(1); }});
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_nav_click.mjs", "w") as f:
            # write as cjs for require
            f.write(node_script.replace(".mjs", ""))
        with sftp.file("/tmp/_nav_click.cjs", "w") as f:
            f.write(node_script)
        sftp.close()

        # copy into web-dev and run with npx playwright
        out = run(
            c,
            "docker cp /tmp/_nav_click.cjs web-dev:/tmp/_nav_click.cjs && "
            "timeout 240 docker exec -w /app web-dev sh -lc '"
            "npm ls playwright >/dev/null 2>&1 || npm install -D playwright@1.49.0 --no-save; "
            "npx playwright install chromium; "
            "node /tmp/_nav_click.cjs"
            "'",
            timeout=280,
        )
        OUT.write_text(token_out + "\n" + out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:6000])
        return 0 if "pageErrors" in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
