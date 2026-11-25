#!/usr/bin/env python3
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


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        out = run(c, "docker restart worker-dev", timeout=90)
        time.sleep(8)
        status = run(c, "docker ps --filter name=worker-dev --format '{{.Status}}'")
        # confirm worker process can import fixed function
        check = run(
            c,
            "timeout 60 docker exec -w /app worker-dev python -c "
            "\"from yuxi.content_cover.ai_cover_prompt import is_case_share_content_type; "
            "assert is_case_share_content_type(content_type_code=None) is False; print('WORKER_OK')\"",
        )
        text = f"restart={out}\nstatus={status}\ncheck={check}"
        OUT.write_text(text, encoding="utf-8")
        print(text)
        return 0 if "WORKER_OK" in check else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
