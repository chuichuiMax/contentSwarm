#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=90):
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
        parts = []
        parts.append("=== AppLayout menu snippet ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "p=Path('/srv/contentswarm/web/src/layouts/AppLayout.vue')\n"
                "print('exists', p.exists(), 'size', p.stat().st_size if p.exists() else None)\n"
                "txt=p.read_text(encoding='utf-8', errors='replace')\n"
                "i=txt.find('内容生产')\n"
                "print(txt[max(0,i-200):i+400])\n"
                "PY"
            )
        )
        parts.append("=== router content routes ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "p=Path('/srv/contentswarm/web/src/router/index.js')\n"
                "txt=p.read_text(encoding='utf-8', errors='replace')\n"
                "for key in ['/content/new','ContentStudio','canAccessScopedPath','beforeEach']:\n"
                "  print(key, txt.find(key))\n"
                "print('--- content block ---')\n"
                "i=txt.find(\"path: '/content'\")\n"
                "print(txt[i:i+1200])\n"
                "PY"
            )
        )
        parts.append("=== vite compile check ContentStudio ===")
        parts.append(
            run(
                c,
                "docker logs web-dev --tail 300 2>&1 | grep -iE 'error|ContentStudio|AppLayout|failed|Internal server' | tail -n 50 || true"
            )
        )
        parts.append("=== curl content page via vite ===")
        parts.append(
            run(
                c,
                "curl -s -m 8 -o /dev/null -w 'vite:%{http_code}\\n' http://127.0.0.1:5173/content/new || echo vite_fail; "
                "curl -s -m 8 -o /dev/null -w '8080:%{http_code}\\n' http://127.0.0.1:8080/content/new || echo 8080_fail"
            )
        )
        parts.append("=== syntax ContentStudioView ===")
        parts.append(
            run(
                c,
                "docker exec web-dev sh -lc 'node --check /app/src/views/ContentStudioView.vue 2>&1 | head -20 || true; "
                "ls -la /app/src/views/ContentStudioView.vue /app/src/layouts/AppLayout.vue'"
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:5000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
