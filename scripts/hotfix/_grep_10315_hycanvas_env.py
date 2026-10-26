from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    repo = Path(__file__).resolve().parents[2]
    for p in (repo / ".deploy" / "10315.password",):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return os.environ.get("DEPLOY_SSH_PASSWORD", "")


def main() -> int:
    pw = _pw()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmd = (
        "grep -E '^(HYCANVAS_|VITE_HMR|VITE_API)' /srv/contentswarm/.env | "
        "sed 's/SECRET=.*/SECRET=***/; s/KEY=.*/KEY=***/'"
    )
    _, o, _ = c.exec_command(cmd, timeout=30)
    print(o.read().decode("utf-8", errors="replace"))
    _, o, _ = c.exec_command(
        "docker exec api-dev env | grep -E '^HYCANVAS_' | sed 's/KEY=.*/KEY=***/' | sort",
        timeout=30,
    )
    print("--- api container ---")
    print(o.read().decode("utf-8", errors="replace"))
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
