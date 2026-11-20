#!/usr/bin/env python3
"""Inspect how 8080 serves SPA and whether /content/new falls through correctly."""
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
        parts.append("=== docker publish ports ===")
        parts.append(run(c, "docker ps --format '{{.Names}}\t{{.Ports}}' | head -n 40"))
        parts.append("=== curl 8080 paths ===")
        parts.append(
            run(
                c,
                "for u in / /content/new /model-manage/employees /src/views/ContentStudioView.vue /assets/index.js; do "
                "echo \"URL $u\"; "
                "curl -s -m 10 -o /tmp/u.out -w 'code:%{http_code} ctype:%{content_type} size:%{size_download} redir:%{redirect_url}\\n' "
                "\"http://127.0.0.1:8080$u\"; "
                "python3 -c \"import pathlib; d=pathlib.Path('/tmp/u.out').read_text(encoding='utf-8',errors='replace'); print(d[:160].replace(chr(10),' '))\"; "
                "done"
            )
        )
        parts.append("=== nginx/caddy/proxy configs ===")
        parts.append(
            run(
                c,
                "ls /srv/contentswarm 2>/dev/null; "
                "find /srv/contentswarm -maxdepth 3 -type f \\( -name '*.conf' -o -name 'docker-compose*.yml' -o -name 'Caddyfile' \\) 2>/dev/null | head -n 40; "
                "grep -RIn --include='*.conf' --include='*.yml' --include='Caddyfile' -e '8080' -e '5173' -e 'try_files' -e 'proxy_pass' "
                "/srv/contentswarm /etc/nginx 2>/dev/null | head -n 80"
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:9000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
