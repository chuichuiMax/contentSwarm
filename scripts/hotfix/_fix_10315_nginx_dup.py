from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    _, o, _ = c.exec_command("grep -rl 'listen 8080' /etc/nginx/ 2>/dev/null", timeout=30)
    print(o.read().decode())
    # Disable duplicate default if both exist
    c.exec_command(
        "test -f /etc/nginx/sites-enabled/default && "
        "grep -q '8080' /etc/nginx/sites-enabled/default && "
        "mv /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.bak.8080 || true",
        timeout=30,
    )
    _, o, e = c.exec_command("nginx -t && systemctl reload nginx", timeout=30)
    print(o.read().decode())
    print(e.read().decode())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
