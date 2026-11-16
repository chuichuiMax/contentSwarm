#!/usr/bin/env python3
"""Deploy KB-topic + CTA + newline-safe title rule; verify tests and task."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
FILES = [
    (
        ROOT / "backend/package/yuxi/content/generation.py",
        "/srv/contentswarm/backend/package/yuxi/content/generation.py",
    ),
    (
        ROOT / "backend/package/yuxi/content/title_rules.py",
        "/srv/contentswarm/backend/package/yuxi/content/title_rules.py",
    ),
    (
        ROOT / "backend/test/unit/content/test_direct_content_generation.py",
        "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py",
    ),
]


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
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
        for local, remote in FILES:
            sftp.put(str(local), remote)
            print("uploaded", local.name)
        sftp.close()
        print(run(c, "python3 -m py_compile /srv/contentswarm/backend/package/yuxi/content/generation.py /srv/contentswarm/backend/package/yuxi/content/title_rules.py && echo SYNTAX_OK"))
        for i in range(20):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print("health", i, code)
            if code.endswith("200"):
                break
            time.sleep(2)
        out = run(
            c,
            "timeout 120 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_direct_content_generation.py -q --tb=line",
            timeout=140,
        )
        Path(__file__).resolve().with_suffix(".pytest.txt").write_text(out, encoding="utf-8")
        print(out[-1500:])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
