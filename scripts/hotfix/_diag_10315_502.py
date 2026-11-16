"""Diagnose 502 on 172.16.103.15 after api restart."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)

    def run(cmd: str, timeout: int = 60) -> str:
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", errors="replace")
        err = e.read().decode("utf-8", errors="replace")
        return (out or err).strip()

    print("=== docker ps ===")
    print(run("docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-dev|web-dev|nginx|hycanvas' || docker ps --format '{{.Names}} {{.Status}}' | head -20"))
    print("=== health local ===")
    print("api:", run("curl -sS -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo fail"))
    print("body:", run("curl -sS -m 8 http://127.0.0.1:5050/api/system/health || true"))
    print("web:", run("curl -sS -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:5173/ || echo fail"))
    print("8080:", run("curl -sS -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ || echo fail"))
    print("=== api logs ===")
    print(run("docker logs api-dev --tail 40 2>&1"))
    status = run("docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' api-dev 2>/dev/null || true")
    print("=== api state ===", status)
    if "restarting" in status or "unhealthy" in status or "exited" in status or run("curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health") != "200":
        print("=== restart api-dev ===")
        print(run("docker restart api-dev", timeout=180))
        time.sleep(12)
        for i in range(12):
            code = run("curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health")
            print(f"wait {i}: {code}")
            if code == "200":
                break
            time.sleep(5)
        print("final:", run("curl -sS -m 10 http://127.0.0.1:5050/api/system/health || true"))
        print(run("docker logs api-dev --tail 20 2>&1"))
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
