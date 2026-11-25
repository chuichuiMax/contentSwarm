#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=180):
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
        src = run(
            c,
            "docker exec api-dev grep -n 'content_type_code or' "
            "/app/package/yuxi/content_cover/ai_cover_prompt.py",
        )
        worker = run(
            c,
            "docker exec worker-dev grep -n 'content_type_code or' "
            "/app/package/yuxi/content_cover/ai_cover_prompt.py",
        )
        check = run(
            c,
            "timeout 60 docker exec -w /app api-dev python -c "
            "\"from yuxi.content_cover.ai_cover_prompt import is_case_share_content_type, "
            "ai_cover_eligible_variants; "
            "assert is_case_share_content_type(content_type_code=None, content_type_name=None) is False; "
            "assert is_case_share_content_type(content_type_code=None, content_type_name='装修案例分享') is True; "
            "assert 'p12' not in ai_cover_eligible_variants(content_type_code=None); "
            "print('OK')\"",
        )
        pytest_out = run(
            c,
            "timeout 180 docker exec -w /app api-dev sh -c "
            "'uv run --group dev --no-sync pytest "
            "test/unit/content/test_cover_generation.py "
            "-k tolerates_none_content_type -q --tb=short'",
            timeout=200,
        )
        text = "\n".join(
            [
                "===api src===",
                src,
                "===worker src===",
                worker,
                "===check===",
                check,
                "===pytest===",
                pytest_out,
            ]
        )
        OUT.write_text(text, encoding="utf-8")
        print(text[-3000:])
        ok = "OK" in check and "passed" in pytest_out and "failed" not in pytest_out.lower()
        return 0 if ok else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
