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
        parts.append("=== transform ContentStudio module via vite ===")
        parts.append(
            run(
                c,
                "curl -s -m 20 'http://127.0.0.1:5173/src/views/ContentStudioView.vue' | head -c 500; echo; "
                "curl -s -m 20 -o /tmp/cs.js -w 'code:%{http_code} size:%{size_download}\\n' "
                "'http://127.0.0.1:5173/src/views/ContentStudioView.vue'"
            )
        )
        parts.append("=== recent vite errors only ===")
        parts.append(
            run(
                c,
                "docker logs web-dev --since 30m 2>&1 | grep -iE 'Internal server error|ERROR|Failed to|Could not' | tail -n 60"
            )
        )
        parts.append("=== contentRequestPayload / base.js exist ===")
        parts.append(
            run(
                c,
                "ls -la /srv/contentswarm/web/src/utils/contentRequestPayload.js "
                "/srv/contentswarm/web/src/apis/base.js "
                "/srv/contentswarm/web/src/views/ContentStudioView.vue 2>&1; "
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "p=Path('/srv/contentswarm/web/src/views/ContentStudioView.vue')\n"
                "txt=p.read_text(encoding='utf-8',errors='replace')\n"
                "for s in ['contentRequestPayload','from \\'@/apis','undefined','SyntaxError']:\n"
                "  print(s, txt.count(s) if s!='undefined' else 'n/a')\n"
                "print('imports:')\n"
                "for line in txt.splitlines()[:80]:\n"
                "  if line.strip().startswith('import '):\n"
                "    print(line)\n"
                "PY"
            )
        )
        parts.append("=== router guard snippet ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "txt=Path('/srv/contentswarm/web/src/router/index.js').read_text(encoding='utf-8')\n"
                "i=txt.find('router.beforeEach')\n"
                "print(txt[i:i+1800])\n"
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
