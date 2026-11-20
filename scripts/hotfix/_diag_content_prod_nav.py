#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=60):
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
        parts.append("=== containers ===")
        parts.append(run(c, "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'web|api|nginx' || true"))
        parts.append("=== web logs ===")
        parts.append(run(c, "docker logs web-dev --tail 80 2>&1 | tail -n 80"))
        parts.append("=== api health ===")
        parts.append(run(c, "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000"))
        parts.append("=== routes grep remote ===")
        parts.append(
            run(
                c,
                "grep -RIn --include='*.js' --include='*.vue' --include='*.ts' "
                "-e '内容生产' -e 'ContentFlow' -e 'content-flow' -e '/agent' "
                "/srv/contentswarm/web/src 2>/dev/null | head -n 80",
            )
        )
        parts.append("=== public index ===")
        parts.append(run(c, "curl -s -m 8 -o /tmp/_idx.html -w '%{http_code}' http://127.0.0.1:8080/ || echo 000; wc -c /tmp/_idx.html; head -c 400 /tmp/_idx.html"))
        parts.append("=== js assets recent errors in nginx/web ===")
        parts.append(run(c, "docker logs web-dev --tail 200 2>&1 | grep -iE 'error|fail|ENOENT|Cannot' | tail -n 40 || true"))
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:4500])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
