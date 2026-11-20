#!/usr/bin/env python3
"""Reproduce 内容生产 click with correct user_token via playwright in web-dev."""
from __future__ import annotations

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
        rows=await conn.fetch('select id,username,role from users order by id asc limit 50')
        uid=None
        for r in rows:
            role=str(r['role'] or '').lower()
            name=str(r['username'] or '').lower()
            if role in {'admin','superadmin'} or name in {'admin','superadmin'}:
                uid=r['id']; print('user', dict(r)); break
        if uid is None:
            uid=rows[0]['id']; print('user_fallback', dict(rows[0]))
    finally:
        await conn.close()
    print('TOKEN', AuthUtils.create_access_token({'sub': str(uid)}))
asyncio.run(main())
'''


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

        node_script = f'''
const fs = require('fs');
async function main() {{
  let chromium;
  try {{
    ({{ chromium }} = require('playwright'));
  }} catch (e) {{
    console.error('NO_PLAYWRIGHT', String(e));
    process.exit(2);
  }}
  const browser = await chromium.launch({{ headless: true, args: ['--no-sandbox','--disable-dev-shm-usage'] }});
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const failed = [];
  const responses = [];
  page.on('pageerror', e => pageErrors.push(String(e && e.stack || e)));
  page.on('console', m => {{ if (m.type() === 'error') consoleErrors.push(m.text()); }});
  page.on('requestfailed', r => failed.push({{ url: r.url(), err: r.failure() && r.failure().errorText }}));
  page.on('response', async r => {{
    const u = r.url();
    if (u.includes('/src/') || u.includes('/api/content') || u.includes('ContentStudio')) {{
      if (r.status() >= 400) responses.push({{ url: u, status: r.status() }});
    }}
  }});

  await page.goto('http://127.0.0.1:5173/login', {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  await page.evaluate(token => {{
    localStorage.setItem('user_token', token);
  }}, {token!r});

  await page.goto('http://127.0.0.1:5173/model-manage/employees', {{ waitUntil: 'domcontentloaded', timeout: 90000 }});
  await page.waitForTimeout(2500);
  const before = await page.evaluate(() => ({{
    href: location.href,
    hasEmployees: (document.body.innerText || '').includes('员工编码'),
    navTexts: [...document.querySelectorAll('a.nav-item, .nav-item')].map(el => (el.textContent || '').trim()).slice(0, 15),
  }}));

  const clicked = await page.evaluate(() => {{
    const items = [...document.querySelectorAll('a.nav-item')];
    const target = items.find(el => (el.textContent || '').includes('内容生产'));
    if (!target) return {{ ok: false, reason: 'not found', texts: items.map(i => (i.textContent || '').trim()).slice(0, 20) }};
    const href = target.getAttribute('href');
    target.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
    return {{ ok: true, href, hrefBefore: location.href }};
  }});

  // wait for either route change or stay
  for (let i = 0; i < 20; i++) {{
    await page.waitForTimeout(500);
    const href = page.url();
    if (href.includes('/content/')) break;
  }}

  const after = await page.evaluate(() => ({{
    href: location.href,
    text: (document.body.innerText || '').slice(0, 800),
    activeNav: [...document.querySelectorAll('.nav-item.active')].map(el => (el.textContent || '').trim()),
    hasStudio: !!(document.querySelector('.content-studio') || document.querySelector('[class*=content-studio]') || (document.body.innerText || '').includes('创作') || (document.body.innerText || '').includes('内容类型')),
    hasEmployees: (document.body.innerText || '').includes('员工编码'),
  }}));

  // direct navigation fallback
  await page.goto('http://127.0.0.1:5173/content/new', {{ waitUntil: 'domcontentloaded', timeout: 90000 }});
  await page.waitForTimeout(3500);
  const direct = await page.evaluate(() => ({{
    href: location.href,
    text: (document.body.innerText || '').slice(0, 800),
    hasEmployees: (document.body.innerText || '').includes('员工编码'),
    hasStudioHints: (document.body.innerText || '').includes('内容类型') || (document.body.innerText || '').includes('创作目标') || (document.body.innerText || '').includes('行业'),
  }}));

  const result = {{ before, clicked, after, direct, pageErrors: pageErrors.slice(0, 20), consoleErrors: consoleErrors.slice(0, 40), failed: failed.slice(0, 30), badResponses: responses.slice(0, 30) }};
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
}}
main().catch(e => {{ console.error(String(e && e.stack || e)); process.exit(1); }});
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_nav_click2.cjs", "w") as f:
            f.write(node_script)
        sftp.close()

        # Prefer already-installed playwright; avoid long chromium download if possible
        out = run(
            c,
            "docker cp /tmp/_nav_click2.cjs web-dev:/tmp/_nav_click2.cjs && "
            "timeout 120 docker exec -w /app web-dev sh -lc '"
            "if ! node -e \"require(\\\"playwright\\\")\" >/dev/null 2>&1; then "
            "  npm install -D playwright-core@1.49.0 --no-save --prefer-offline; "
            "fi; "
            "node /tmp/_nav_click2.cjs"
            "'",
            timeout=150,
        )
        # If playwright missing browsers, try system chromium via playwright-core + executablePath
        if "NO_PLAYWRIGHT" in out or "Executable doesn't exist" in out or "browserType.launch" in out:
            node_script2 = node_script.replace(
                "chromium.launch({ headless: true, args: ['--no-sandbox','--disable-dev-shm-usage'] })",
                "chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/chromium', args: ['--no-sandbox','--disable-dev-shm-usage'] })",
            ).replace(
                "require('playwright')",
                "require('playwright-core')",
            )
            sftp = c.open_sftp()
            with sftp.file("/tmp/_nav_click2b.cjs", "w") as f:
                f.write(node_script2)
            sftp.close()
            out2 = run(
                c,
                "docker cp /tmp/_nav_click2b.cjs web-dev:/tmp/_nav_click2b.cjs && "
                "timeout 120 docker exec -w /app web-dev sh -lc '"
                "export CHROME_PATH=$(command -v chromium || command -v chromium-browser || command -v google-chrome || true); "
                "echo CHROME_PATH=$CHROME_PATH; "
                "npm install -D playwright-core@1.49.0 --no-save --prefer-offline >/tmp/pw.log 2>&1 || true; "
                "node /tmp/_nav_click2b.cjs"
                "'",
                timeout=150,
            )
            out = out + "\n=== RETRY ===\n" + out2

        text = token_out + "\n" + out
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:9000])
        return 0 if '"after"' in out or '"direct"' in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
