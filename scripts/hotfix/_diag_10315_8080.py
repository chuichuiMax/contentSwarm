"""Diagnose 8080 / web-dev on 103.15."""
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
    return ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmds = [
        "systemctl is-active nginx; ss -tlnp | grep -E ':8080|:5173|:5050' || true",
        "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'web|api|nginx' || true",
        "curl -s -o /dev/null -w '127.0.0.1:8080/ %{http_code} time=%{time_total}\\n' --max-time 15 http://127.0.0.1:8080/ || echo curl8080_fail",
        "curl -s -o /dev/null -w '127.0.0.1:5173/ %{http_code} time=%{time_total}\\n' --max-time 15 http://127.0.0.1:5173/ || echo curl5173_fail",
        "curl -s -o /dev/null -w '127.0.0.1:5050/api/system/health %{http_code}\\n' --max-time 10 http://127.0.0.1:5050/api/system/health || true",
        "docker logs web-dev --tail 40 2>&1",
        "nginx -t 2>&1",
        "tail -20 /var/log/nginx/error.log 2>/dev/null || true",
    ]
    for cmd in cmds:
        print(">>>", cmd[:85])
        _, o, e = c.exec_command(cmd, timeout=120)
        out = o.read().decode("utf-8", errors="replace")
        err = e.read().decode("utf-8", errors="replace")
        if out.strip():
            print(out)
        if err.strip():
            print("stderr:", err[:500])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
