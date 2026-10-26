"""Diagnose /hycanvas on 103.15 test server."""
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
    for p in (repo / ".deploy" / "10315.password", repo / ".deploy" / "password"):
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
        "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'web|api|hycanvas|nginx' || true",
        "curl -s -o /dev/null -w 'vite5173 /hycanvas %{http_code} time=%{time_total}\\n' --max-time 15 http://127.0.0.1:5173/hycanvas || true",
        "curl -s -o /dev/null -w 'nginx8080 /hycanvas %{http_code} time=%{time_total}\\n' --max-time 15 http://127.0.0.1:8080/hycanvas || true",
        "curl -s -o /dev/null -w 'api health %{http_code}\\n' --max-time 10 http://127.0.0.1:5050/api/system/health || true",
        "curl -s -o /dev/null -w 'hycanvas8005 %{http_code}\\n' --max-time 10 http://127.0.0.1:8005/healthz || true",
        "docker logs web-dev --tail 30 2>&1",
        "docker logs api-dev --tail 20 2>&1",
        "docker logs contentswarm-hycanvas-app-1 --tail 20 2>&1 || docker logs hycanvas-app --tail 20 2>&1 || true",
    ]
    for cmd in cmds:
        print(">>>", cmd[:90])
        _, o, e = c.exec_command(cmd, timeout=120)
        out = o.read().decode("utf-8", errors="replace")
        err = e.read().decode("utf-8", errors="replace")
        if out.strip():
            print(out)
        if err.strip():
            print("stderr:", err[:400])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
