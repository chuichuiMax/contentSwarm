#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().with_suffix(".out.txt")

FILES = [
    (
        REPO / "backend/package/yuxi/content_cover/ai_cover_prompt.py",
        "/srv/contentswarm/backend/package/yuxi/content_cover/ai_cover_prompt.py",
        "/app/package/yuxi/content_cover/ai_cover_prompt.py",
    ),
    (
        REPO / "backend/package/yuxi/services/content_direct_worker.py",
        "/srv/contentswarm/backend/package/yuxi/services/content_direct_worker.py",
        "/app/package/yuxi/services/content_direct_worker.py",
    ),
    (
        REPO / "backend/package/yuxi/content/control/visual_template_fields.py",
        "/srv/contentswarm/backend/package/yuxi/content/control/visual_template_fields.py",
        "/app/package/yuxi/content/control/visual_template_fields.py",
    ),
    (
        REPO / "backend/test/unit/content/test_cover_generation.py",
        "/srv/contentswarm/backend/test/unit/content/test_cover_generation.py",
        "/app/test/unit/content/test_cover_generation.py",
    ),
]

CHECK = r'''
from yuxi.content_cover.ai_cover_prompt import (
    ai_cover_eligible_variants,
    is_case_share_content_type,
)

assert is_case_share_content_type(content_type_code=None, content_type_name=None) is False
assert is_case_share_content_type(content_type_code=None, content_type_name="装修案例分享") is True
pool = ai_cover_eligible_variants(content_type_code=None, content_type_name=None)
assert "p12" not in pool
print("OK_NONE_STRIP")
'''


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
    lines: list[str] = []
    try:
        sftp = c.open_sftp()
        for local, host_path, _ in FILES:
            remote_tmp = f"/tmp/{local.name}"
            sftp.put(str(local), remote_tmp)
            lines.append(f"PUT {local.name}")
        with sftp.file("/tmp/_check_none_strip.py", "w") as f:
            f.write(CHECK)
        sftp.close()

        for local, host_path, container_path in FILES:
            remote_tmp = f"/tmp/{local.name}"
            for container in ("api-dev", "worker-dev"):
                out = run(
                    c,
                    f"cp {remote_tmp} {host_path} && "
                    f"docker cp {remote_tmp} {container}:{container_path}",
                    timeout=60,
                )
                lines.append(f"SYNC {container}:{container_path} {out!r}")

        check = run(
            c,
            "docker cp /tmp/_check_none_strip.py api-dev:/tmp/_check_none_strip.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_check_none_strip.py",
            timeout=90,
        )
        lines.append("===check===")
        lines.append(check)

        pytest_out = run(
            c,
            "timeout 120 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_cover_generation.py "
            "-k 'tolerates_none_content_type or includes_p12_for_case_share' -q --tb=short",
            timeout=150,
        )
        lines.append("===pytest===")
        lines.append(pytest_out)

        text = "\n".join(lines)
        OUT.write_text(text, encoding="utf-8")
        print(text[-2500:])
        return 0 if "OK_NONE_STRIP" in check and "failed" not in pytest_out.lower() else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
