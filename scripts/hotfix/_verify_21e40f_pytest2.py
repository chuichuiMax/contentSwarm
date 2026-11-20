#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=220):
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
        print(run(c, "docker exec -w /app api-dev uv run --no-sync python -m pytest --version", timeout=40))
        out = run(
            c,
            "timeout 200 docker exec -w /app api-dev uv run --no-sync python -m pytest "
            "test/unit/content/test_direct_content_generation.py "
            "-k 'case_body_detects or case_share_strips or case_share_generation_keeps "
            "or case_share_rewrites or uses_only_requested' -q --tb=line",
            timeout=220,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out[-1600:].encode("ascii", "replace").decode("ascii"))
        return 0 if "passed" in out and "failed" not in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
