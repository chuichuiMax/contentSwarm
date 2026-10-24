"""Ensure production .env AI_COVER_OSS_SUBDIR is UTF-8 AI封面."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.17"
BASE = "/opt/contentSwarm"
SUBDIR = "AI封面"
PATCH: dict[str, str] = {
    "AI_COVER_OSS_BUCKET": "hongyang01",
    "AI_COVER_OSS_REGION": "cn-beijing",
    "AI_COVER_OSS_ENDPOINT": "oss-cn-beijing.aliyuncs.com",
    "AI_COVER_OSS_PUBLIC_BASE_URL": "https://hongyang01.oss-cn-beijing.aliyuncs.com",
    "AI_COVER_OSS_PREFIX": "AI-cover/",
    "AI_COVER_OSS_SUBDIR": SUBDIR,
}


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    path = f"{BASE}/.env"
    with sftp.open(path, "r") as f:
        text = f.read().decode("utf-8", errors="replace")
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        matched = False
        for key, value in PATCH.items():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                seen.add(key)
                matched = True
                break
        if not matched:
            lines.append(line)
    for key, value in PATCH.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    with sftp.open(path, "w") as f:
        f.write(("\n".join(lines).rstrip() + "\n").encode("utf-8"))
    sftp.close()
    c.exec_command(f"cd {BASE} && docker compose --env-file .env up -d --no-deps api web worker", timeout=180)
    import time

    time.sleep(8)
    check = (
        "docker exec api-dev python -c \"import os; "
        "s=os.getenv('AI_COVER_OSS_SUBDIR') or ''; "
        "print('ok' if s=='AI封面' else repr(s))\""
    )
    _, o, _ = c.exec_command(check, timeout=60)
    print(o.read().decode("utf-8", errors="replace").strip())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
