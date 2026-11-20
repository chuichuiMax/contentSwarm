#!/usr/bin/env python3
"""Install playwright lightly? Or use node in web-dev to evaluate router.

Fallback: use chromium from host if available; else fetch main graph of imports via vite optimize.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=120):
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


NODE = r'''
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  const consoleMsgs = [];
  const pageErrors = [];
  const failed = [];
  page.on('console', (msg) => {
    if (['error', 'warning'].includes(msg.type())) consoleMsgs.push({ type: msg.type(), text: msg.text() });
  });
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  page.on('requestfailed', (req) => failed.push({ url: req.url(), err: req.failure() }));
  await page.goto('http://127.0.0.1:5173/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  // no token: just ensure app boots
  await page.wait_for_timeout(1500);
  const loginText = await page.evaluate(() => (document.body.innerText || '').slice(0, 200));
  await page.goto('http://127.0.0.1:5173/content/new', { waitUntil: 'networkidle', timeout: 90000 });
  await page.wait_for_timeout(2500);
  const info = await page.evaluate(() => ({
    href: location.href,
    text: (document.body.innerText || '').slice(0, 400),
  }));
  console.log(JSON.stringify({ loginText, info, pageErrors, consoleMsgs: consoleMsgs.slice(0, 30), failed: failed.slice(0, 20) }, null, 2));
  await browser.close();
})().catch((e) => {
  console.error('FAIL', e);
  process.exit(1);
});
'''


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        parts = []
        parts.append("=== playwright in web-dev? ===")
        parts.append(
            run(
                c,
                "docker exec web-dev sh -lc 'ls node_modules/playwright 2>/dev/null | head; "
                "ls /root/.cache/ms-playwright 2>/dev/null | head; "
                "node -e \"try{require(\"playwright\");console.log(\"pw_ok\")}catch(e){console.log(\"pw_miss\",e.message)}\"'",
                timeout=40,
            )
        )
        # Check vite transform of AppLayout for errors
        parts.append("=== AppLayout transform ===")
        parts.append(
            run(
                c,
                "curl -s -m 20 -o /tmp/al.js -w '%{http_code} %{size_download}\\n' "
                "'http://127.0.0.1:5173/src/layouts/AppLayout.vue'; "
                "python3 -c \"t=open('/tmp/al.js',encoding='utf-8',errors='replace').read();"
                "print('err', 'Internal Server Error' in t or 'Failed to resolve' in t); print(t[:120])\"",
            )
        )
        # Scan missing imports for ContentStudio more carefully
        parts.append("=== missing imports ===")
        parts.append(
            run(
                c,
                "python3 <<'PY'\n"
                "from pathlib import Path\n"
                "import re\n"
                "txt=Path('/srv/contentswarm/web/src/views/ContentStudioView.vue').read_text(encoding='utf-8')\n"
                "imps=re.findall(r\"from '(@/[^']+)'\", txt)\n"
                "root=Path('/srv/contentswarm/web/src')\n"
                "missing=[]\n"
                "for imp in imps:\n"
                "  rel=imp[2:]\n"
                "  cands=[root/rel, root/f'{rel}.js', root/f'{rel}.vue', root/f'{rel}.ts', root/rel/'index.js', root/rel/'index.ts']\n"
                "  if not any(c.exists() for c in cands):\n"
                "    missing.append(imp)\n"
                "print('missing', missing)\n"
                "PY"
            )
        )
        # Compare employees route component vs content - any global error boundary?
        parts.append("=== main.js error handler ===")
        parts.append(
            run(
                c,
                "python3 <<'PY'\n"
                "from pathlib import Path\n"
                "print(Path('/srv/contentswarm/web/src/main.js').read_text(encoding='utf-8')[:1500])\n"
                "PY"
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:5500])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
