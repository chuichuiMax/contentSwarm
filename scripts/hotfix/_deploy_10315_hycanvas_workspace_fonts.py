"""Deploy HyCanvas workspace shared fonts to 103.15 and smoke-test upload API."""
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
    "apps/hycanvas/frontend/src/components/editor/EditorPanels.tsx",
    "apps/hycanvas/frontend/src/components/editor/ToolRail.tsx",
    "apps/hycanvas/frontend/src/components/RequireAuth.tsx",
    "apps/hycanvas/frontend/src/lib/fontProvider.ts",
    "apps/hycanvas/frontend/src/lib/managedAuth.ts",
    "apps/hycanvas/frontend/src/locales/en.json",
    "apps/hycanvas/frontend/public/locales/zh-cn.json",
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
        f"grep -c refreshWorkspaceFonts {BASE}/apps/hycanvas/frontend/src/components/editor/EditorPanels.tsx",
        f"grep -c 'TextPanel workspaceId' {BASE}/apps/hycanvas/frontend/src/components/editor/ToolRail.tsx",
        "curl -s http://127.0.0.1:8080/locales/zh-cn.json | grep -c workspace_fonts || true",
        "curl -s -o /dev/null -w 'editor3000 %{http_code}\\n' --max-time 20 http://127.0.0.1:3000/editor || true",
        "docker ps --filter name=hycanvas --format '{{.Names}} {{.Status}}'",
    ]
    for cmd in checks:
        out, err, code = _run(c, cmd)
        line = (out or err).strip()
        print(">>>", cmd[:72])
        if line:
            print(line)
        if code != 0 and "grep -c" not in cmd and "|| true" not in cmd:
            print(f"exit {code}", file=sys.stderr)

    remote_smoke = REPO / "scripts/hotfix/_remote_hycanvas_font_smoke.py"
    _run(c, f"mkdir -p {BASE}/scripts/hotfix")
    sftp = c.open_sftp()
    sftp.put(str(remote_smoke), f"{BASE}/scripts/hotfix/_remote_hycanvas_font_smoke.py")
    sftp.close()
    out, err, code = _run(c, f"python3 {BASE}/scripts/hotfix/_remote_hycanvas_font_smoke.py", timeout=300)
    print(">>> API smoke")
    print(out.strip())
    if err.strip():
        print(err.strip(), file=sys.stderr)

    c.close()
    if code != 0:
        print("smoke test failed", file=sys.stderr)
        return code
    print("done — Next dev hot-reloads; hard-refresh /hycanvas editor to pick up UI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
