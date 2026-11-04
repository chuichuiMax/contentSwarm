"""Quick HyCanvas health on 103.15."""
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
    for p in (Path(__file__).resolve().parents[2] / ".deploy" / "10315.password",):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    raise SystemExit("missing DEPLOY_SSH_PASSWORD")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=_pw(), timeout=30)
    cmds = [
        "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'hycanvas|web-dev|api-dev|nginx' || true",
        "docker inspect --format 'health={{.State.Health.Status}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}' contentswarm-hycanvas-app-1",
        "docker logs --tail 25 contentswarm-hycanvas-app-1 2>&1",
        "curl -sS -m 5 -o /dev/null -w 'healthz=%{http_code}\\n' http://127.0.0.1:8005/healthz || true",
        "curl -sS -m 5 -o /dev/null -w 'next=%{http_code}\\n' http://127.0.0.1:3000/ || true",
        "dmesg -T 2>/dev/null | grep -i 'killed process' | tail -5 || true",
    ]
    for cmd in cmds:
        print(f"\n=== {cmd} ===")
        _, o, e = c.exec_command(cmd, timeout=90)
        print((o.read() + e.read()).decode("utf-8", "replace"))
    c.close()


if __name__ == "__main__":
    main()
