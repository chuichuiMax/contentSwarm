#!/usr/bin/env python3
"""Run playwright in official image against host :8080 to reproduce content nav."""
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
        rows=await conn.fetch("select id,username,role from users where role ilike '%admin%' order by id asc limit 5")
        print('user', dict(rows[0]))
        uid=rows[0]['id']
    finally:
        await conn.close()
    print('TOKEN', AuthUtils.create_access_token({'sub': str(uid)}))
asyncio.run(main())
'''

NODE_TEMPLATE = r'''
const { chromium } = require('playwright');
(async () => {
  const TOKEN = process.env.NAV_TOKEN;
  const BASE = process.env.NAV_BASE || 'http://172.17.0.1:8080';
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox','--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const failed = [];
  const bad = [];
  page.on('pageerror', e => pageErrors.push(String(e && e.stack || e)));
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('requestfailed', r => failed.push({ url: r.url(), err: r.failure() && r.failure().errorText }));
  page.on('response', r => {
    const u = r.url();
    if (r.status() >= 400 && (u.includes('/src/') || u.includes('/api/content') || u.includes('ContentStudio'))) {
      bad.push({ url: u, status: r.status() });
    }
  });
  await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.evaluate(t => localStorage.setItem('user_token', t), TOKEN);
  await page.goto(BASE + '/model-manage/employees', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(3000);
  const before = await page.evaluate(() => ({
    href: location.href,
    hasEmployees: (document.body.innerText || '').includes('员工编码'),
    nav: [...document.querySelectorAll('a.nav-item')].map(el => (el.textContent || '').trim()).slice(0, 15),
  }));
  const clicked = await page.evaluate(() => {
    const items = [...document.querySelectorAll('a.nav-item')];
    const t = items.find(el => (el.textContent || '').includes('内容生产'));
    if (!t) return { ok: false, texts: items.map(i => (i.textContent || '').trim()).slice(0, 20) };
    t.click();
    return { ok: true, href: t.getAttribute('href') };
  });
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(500);
    if (page.url().includes('/content/')) break;
  }
  const after = await page.evaluate(() => ({
    href: location.href,
    hasEmployees: (document.body.innerText || '').includes('员工编码'),
    text: (document.body.innerText || '').slice(0, 1000),
    activeNav: [...document.querySelectorAll('.nav-item.active')].map(el => (el.textContent || '').trim()),
  }));
  await page.goto(BASE + '/content/new', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await page.waitForTimeout(6000);
  const direct = await page.evaluate(() => ({
    href: location.href,
    hasEmployees: (document.body.innerText || '').includes('员工编码'),
    text: (document.body.innerText || '').slice(0, 1000),
  }));
  console.log(JSON.stringify({ before, clicked, after, direct, pageErrors: pageErrors.slice(0, 30), consoleErrors: consoleErrors.slice(0, 50), failed: failed.slice(0, 30), bad: bad.slice(0, 30) }, null, 2));
  await browser.close();
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
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
        with sftp.file("/tmp/_nav_click_docker.cjs", "w") as f:
            f.write(NODE_TEMPLATE)
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

        sftp = c.open_sftp()
        with sftp.file("/tmp/_nav_token.env", "w") as f:
            f.write(f"NAV_TOKEN={token}\nNAV_BASE=http://127.0.0.1:8080\n")
        sftp.close()

        # Prefer existing local image; pull only if missing
        out = run(
            c,
            "IMG=mcr.microsoft.com/playwright:v1.49.0-jammy; "
            "docker image inspect $IMG >/dev/null 2>&1 || docker pull $IMG; "
            "docker run --rm --network host "
            "--env-file /tmp/_nav_token.env "
            "-v /tmp/_nav_click_docker.cjs:/tmp/_nav_click_docker.cjs:ro "
            "$IMG node /tmp/_nav_click_docker.cjs",
            timeout=280,
        )
        text = token_out + "\n" + out
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:12000])
        return 0 if '"after"' in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
