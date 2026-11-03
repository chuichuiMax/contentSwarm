"""Deploy HyCanvas hide ContentSwarm automation covers from dashboard recents (103.15)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
FILES = (
    "apps/hycanvas/backend/internal/persistence/file.go",
    "apps/hycanvas/backend/internal/persistence/backfill_contentswarm_covers.go",
    "apps/hycanvas/backend/internal/persistence/persistence.go",
    "apps/hycanvas/backend/internal/templates/cover_automation.go",
    "apps/hycanvas/backend/internal/templates/text_autofit.go",
    "apps/hycanvas/backend/cmd/api/main.go",
    "apps/hycanvas/frontend/src/components/dashboard/DashboardApp.tsx",
)


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        direct = os.environ.get(key, "").strip()
        if direct:
            return direct
    repo = Path(__file__).resolve().parents[2]
    for p in (repo / ".deploy" / "10315.password", Path.home() / ".contentSwarm_test_ssh"):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def _run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", errors="replace").strip()
    err = e.read().decode("utf-8", errors="replace").strip()
    if err and not out:
        return err
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or .deploy/10315.password", file=sys.stderr)
        return 1
    repo = Path(__file__).resolve().parents[2]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in FILES:
        src = repo / rel
        if not src.is_file():
            print(f"missing {src}", file=sys.stderr)
            return 1
        remote = f"{BASE}/{PurePosixPath(rel)}"
        sftp.put(str(src), remote)
        print("OK", rel)
    sftp.close()

    checks = (
        f"grep -c BackfillContentSwarmCoverDocKinds {BASE}/apps/hycanvas/backend/cmd/api/main.go",
        f"grep -c excludeAutomationCovers {BASE}/apps/hycanvas/frontend/src/components/dashboard/DashboardApp.tsx",
        f"grep -c DocKindContentSwarmCover {BASE}/apps/hycanvas/backend/internal/persistence/file.go",
    )
    for cmd in checks:
        print("verify:", _run(c, cmd))

    hc = _run(c, "docker ps --format '{{.Names}}' | grep hycanvas-app | head -1") or "contentswarm-hycanvas-app-1"
    print("hycanvas container:", hc)
    print(_run(c, f"docker restart {hc}", timeout=180))
    time.sleep(12)
    print("backfill log:", _run(c, f"docker logs {hc} 2>&1 | grep 'contentswarm cover doc_kind backfill' | tail -3"))
    print("healthz:", _run(c, "curl -sS -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8005/healthz"))
    print("api:", _run(c, "curl -sS -m 15 http://127.0.0.1:5050/api/system/health"))
    c.close()
    print("done — hard refresh visual creation / HyCanvas dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
