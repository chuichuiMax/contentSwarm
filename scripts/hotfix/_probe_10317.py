"""One-shot probe 103.17 production layout."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = "172.16.103.17"


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmds = [
        "ls -d /opt/contentSwarm /www/wwwroot/yuxi /srv/contentswarm 2>/dev/null || true",
        "docker ps --format '{{.Names}} {{.Ports}}' | grep -E 'api|5050|8090' | head -10",
        "curl -s -o /dev/null -w 'health %{http_code}\\n' http://127.0.0.1:5050/api/system/health || true",
        "curl -s -o /dev/null -w '8090 %{http_code}\\n' http://127.0.0.1:8090/api/system/health || true",
        "grep AI_COVER /opt/contentSwarm/.env 2>/dev/null | sed 's/SECRET=.*/SECRET=***/' || true",
        "grep AI_COVER /www/wwwroot/yuxi/.env.prod 2>/dev/null | sed 's/SECRET=.*/SECRET=***/' || true",
    ]
    for cmd in cmds:
        print(">>>", cmd)
        _, o, e = c.exec_command(cmd, timeout=60)
        print(o.read().decode())
        err = e.read().decode().strip()
        if err:
            print("stderr:", err)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
