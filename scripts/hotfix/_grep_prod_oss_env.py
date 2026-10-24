from __future__ import annotations

import os
import sys

import paramiko

HOST = "172.16.103.17"


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmd = "grep -E '^(OSS_|AI_COVER_|STORAGE_BACKEND)' /opt/contentSwarm/.env | sed 's/SECRET=.*/SECRET=***/'"
    _, o, _ = c.exec_command(cmd, timeout=30)
    print(o.read().decode("utf-8", errors="replace"))
    _, o, _ = c.exec_command(
        "docker exec api-dev env | grep -E '^(OSS_|AI_COVER_)' | sed 's/SECRET=.*/SECRET=***/' | sort",
        timeout=30,
    )
    print("--- container ---")
    print(o.read().decode("utf-8", errors="replace"))
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
