#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"


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
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        cmds = [
            "docker exec api-dev sh -lc 'ls -la /app | head'",
            "docker exec api-dev sh -lc 'ls /app/.venv/bin 2>/dev/null | head -40'",
            "docker exec api-dev sh -lc 'find / -name pytest -type f 2>/dev/null | head -20'",
            "docker exec api-dev sh -lc 'uv run --no-sync which pytest; uv tree 2>/dev/null | head -5; cat /app/pyproject.toml | head -40'",
            "docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | head -20",
        ]
        for cmd in cmds:
            print("===", cmd)
            print(run(c, cmd, timeout=80)[:2000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
