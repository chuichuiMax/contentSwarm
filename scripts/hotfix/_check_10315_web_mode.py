from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip() or Path(
        __file__
    ).resolve().parents[2].joinpath(".deploy", "10315.password").read_text(encoding="utf-8").strip()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    for cmd in (
        "docker exec web-dev ps aux | head -5",
        "docker exec web-dev ls -la /app/dist 2>/dev/null | head -5 || echo no_dist",
        "curl -s http://127.0.0.1:5050/api/system/health",
    ):
        _, o, _ = c.exec_command(cmd, timeout=30)
        print(">>>", cmd)
        print(o.read().decode()[:500])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
