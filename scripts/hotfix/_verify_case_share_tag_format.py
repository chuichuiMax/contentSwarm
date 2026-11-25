#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
FILES = [
    Path(__file__).resolve().parents[2] / "backend/package/yuxi/content/generation.py",
    Path(__file__).resolve().parents[2] / "backend/test/unit/content/test_direct_content_generation.py",
]
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE = r'''
from yuxi.content import generation

assert generation.case_share_topics(
    ["#现代"],
    '{"businessVariables":{"所在区域":"长沙","设计风格":""}}',
    image_attached=True,
) == ["现代风", "长沙装修", "装修", "装修案例"]
assert generation.case_share_topics(
    ["#现代"],
    '{"businessVariables":{"所在区域":"长沙市","设计风格":"原木"}}',
    image_attached=True,
) == ["原木风", "长沙装修", "装修", "装修案例"]
assert generation.case_share_topics(
    [],
    '{"businessVariables":{"所在区域":"长沙市","设计风格":"江南印象"}}',
    image_attached=False,
) == ["江南印象", "长沙装修", "装修", "装修案例"]
print("OK")
'''


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
        sftp = c.open_sftp()
        sftp.put(str(FILES[0]), "/tmp/generation.py")
        with sftp.file("/tmp/_check_tags.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/generation.py api-dev:/tmp/generation.py && "
            "docker cp /tmp/_check_tags.py api-dev:/tmp/_check_tags.py && "
            "docker exec api-dev sh -c '"
            "cp /tmp/generation.py /app/package/yuxi/content/generation.py && "
            "python /tmp/_check_tags.py'",
            timeout=90,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out)
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
