"""Prod: mount updated nginx default.conf and recreate web; optional host 8080 realtime."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.17"
BASE = "/opt/contentSwarm"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for p in (
        REPO / ".deploy" / "10317.password",
        REPO / ".deploy" / "prod.password",
        REPO / ".deploy" / "10315.password",
    ):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in (
        "docker/nginx/default.conf",
        "docker/nginx/contentswarm-host-dev-proxy.conf",
    ):
        sftp.put(str(REPO / rel), f"{BASE}/{rel}")
        print("OK", rel)
    sftp.close()
    cmds = [
        f"cd {BASE} && docker compose --env-file .env up -d --force-recreate --no-deps web",
        "docker ps --filter name=hycanvas-app --format '{{.Names}} {{.Status}}'",
        "curl -s -o /dev/null -w 'realtime_http %{http_code}\\n' -H 'Connection: Upgrade' -H 'Upgrade: websocket' http://127.0.0.1/realtime || true",
    ]
    for cmd in cmds:
        print(">>>", cmd[:90])
        _, o, e = c.exec_command(cmd, timeout=300)
        out = o.read().decode("utf-8", errors="replace").strip()
        if out:
            print(out)
        err = e.read().decode("utf-8", errors="replace").strip()
        if err:
            print(err[:400], file=sys.stderr)
    time.sleep(3)
    _, o, _ = c.exec_command(
        f"grep -c '/realtime' {BASE}/docker/nginx/default.conf",
        timeout=30,
    )
    print("default.conf /realtime blocks:", o.read().decode().strip())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
