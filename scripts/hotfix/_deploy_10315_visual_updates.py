"""Sync visual creation updates to 103.15: 所在区域 field, font fixes, template fill backend."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
FILES = (
    # HyCanvas UI
    "apps/hycanvas/frontend/src/components/dashboard/DashboardApp.tsx",
    "apps/hycanvas/frontend/src/components/editor/SaveAsTemplateDialog.tsx",
    "apps/hycanvas/frontend/src/components/editor/EditorPanels.tsx",
    "apps/hycanvas/frontend/src/components/editor/ToolRail.tsx",
    "apps/hycanvas/frontend/src/components/RequireAuth.tsx",
    "apps/hycanvas/frontend/src/lib/fontProvider.ts",
    "apps/hycanvas/frontend/src/lib/managedAuth.ts",
    "apps/hycanvas/frontend/public/locales/zh-cn.json",
    "apps/hycanvas/frontend/src/locales/en.json",
    "apps/hycanvas/packages/templates/src/types.ts",
    "apps/hycanvas/packages/sdk/src/index.ts",
    # HyCanvas backend (templates)
    "apps/hycanvas/backend/internal/templates/repository.go",
    "apps/hycanvas/backend/internal/templates/templates.go",
    # Content API: template field resolution
    "backend/package/yuxi/content/control/visual_template_fields.py",
)


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for p in (REPO / ".deploy" / "10315.password", Path(__file__).resolve().parent / "10315.password"):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def _run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[str, str, int]:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", errors="replace")
    err = e.read().decode("utf-8", errors="replace")
    code = o.channel.recv_exit_status()
    return out, err, code


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or .deploy/10315.password", file=sys.stderr)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in FILES:
        src = REPO / rel
        if not src.is_file():
            print(f"missing {src}", file=sys.stderr)
            return 1
        remote = f"{BASE}/{PurePosixPath(rel)}"
        remote_dir = str(PurePosixPath(remote).parent)
        try:
            sftp.stat(remote_dir)
        except OSError:
            c.exec_command(f"mkdir -p '{remote_dir}'", timeout=30)
        sftp.put(str(src), remote)
        print("OK", rel)
    sftp.close()

    checks = [
        f"grep -c 'service_region' {BASE}/apps/hycanvas/frontend/src/components/editor/SaveAsTemplateDialog.tsx",
        f"grep -c service_region {BASE}/backend/package/yuxi/content/control/visual_template_fields.py",
        "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-dev|hycanvas-app' || true",
        "curl -sS -m 5 -o /dev/null -w 'healthz=%{http_code}\\n' http://127.0.0.1:8005/healthz",
        "curl -sS -m 5 -o /dev/null -w 'api=%{http_code}\\n' http://127.0.0.1:5050/api/system/health",
    ]
    for cmd in checks:
        out, err, code = _run(c, cmd)
        line = (out or err).strip()
        print(">>>", cmd[:76])
        if line:
            print(line)
        if code != 0 and "grep -c" not in cmd:
            print(f"warning exit {code}", file=sys.stderr)

    print(">>> restart hycanvas-app (Go templates) + api-dev (visual_template_fields.py)")
    out, err, code = _run(c, "docker restart contentswarm-hycanvas-app-1 api-dev", timeout=240)
    if out.strip():
        print(out.strip())
    if err.strip():
        print(err.strip())
    time.sleep(6)
    out, _, _ = _run(c, "curl -sS -m 15 http://127.0.0.1:5050/api/system/health")
    print(">>> api health after restart:", out.strip() or "(empty)")

    c.close()
    print("done — Ctrl+F5 on http://124.232.148.28:8080/hycanvas then re-open save-template dialog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
