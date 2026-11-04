"""Verify test 8080 proxies /realtime to HyCanvas."""
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
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    pw = _pw()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    for cmd in (
        "grep -A2 'location.*/realtime' /etc/nginx/conf.d/contentswarm-dev-proxy.conf | head -5",
        "curl -s -o /dev/null -w '127.0.0.1:8080/realtime %{http_code}\\n' -H 'Connection: Upgrade' -H 'Upgrade: websocket' 'http://127.0.0.1:8080/realtime?design=test'",
        "curl -s -o /dev/null -w '8005/realtime %{http_code}\\n' -H 'Connection: Upgrade' -H 'Upgrade: websocket' 'http://127.0.0.1:8005/realtime?design=test'",
        "docker ps --filter name=hycanvas --format '{{.Names}} {{.Status}}'",
    ):
        _, o, _ = c.exec_command(cmd, timeout=30)
        print(o.read().decode("utf-8", errors="replace").strip())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
