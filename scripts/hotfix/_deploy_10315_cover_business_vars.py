"""Deploy cover business-variable fill fixes to 103.15 api-dev."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
FILES = (
    "backend/package/yuxi/services/content_service.py",
    "backend/package/yuxi/content/control/visual_template_fields.py",
    "apps/hycanvas/backend/internal/templates/cover_automation.go",
    "apps/hycanvas/backend/internal/templates/instantiate.go",
    "apps/hycanvas/backend/internal/templates/text_autofit.go",
    "apps/hycanvas/backend/internal/persistence/persistence.go",
    "apps/hycanvas/backend/internal/render/automation_text_autofit.go",
    "apps/hycanvas/backend/internal/render/raster.go",
    "apps/hycanvas/backend/internal/httpapi/render.go",
    "apps/hycanvas/frontend/src/lib/fontProvider.ts",
    "web/src/views/HycanvasWorkspaceView.vue",
)


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        direct = os.environ.get(key, "").strip()
        if direct:
            return direct
    repo = Path(__file__).resolve().parents[2]
    for p in (
        repo / ".deploy" / "10315.password",
        Path.home() / ".contentSwarm_test_ssh",
    ):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


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
        f"grep -c businessVariables {BASE}/backend/package/yuxi/services/content_service.py",
        f"grep -c _infer_template_field_role {BASE}/backend/package/yuxi/content/control/visual_template_fields.py",
        f"grep -c _BUSINESS_VARIABLE_LABELS {BASE}/backend/package/yuxi/content/control/visual_template_fields.py",
    )
    for cmd in checks:
        _, o, _ = c.exec_command(cmd, timeout=30)
        print("verify:", o.read().decode().strip())

    verify_py = (
        "docker exec api-dev uv run --no-sync python -c "
        "\"from yuxi.content.control.visual_template_fields import _infer_template_field_role; "
        "print(_infer_template_field_role('', '从业年限'))\""
    )
    _, o, e = c.exec_command(verify_py, timeout=90)
    print("import check:", o.read().decode("utf-8", errors="replace").strip())
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)

    _, o, _ = c.exec_command("docker restart api-dev", timeout=120)
    print(o.read().decode())
    hycanvas = "contentswarm-hycanvas-app-1"
    _, o, _ = c.exec_command(
        f"docker ps --format '{{{{.Names}}}}' | grep -E 'hycanvas-app|hycanvas' | head -1",
        timeout=30,
    )
    hc = o.read().decode().strip() or hycanvas
    touch = (
        f"docker exec {hc} sh -c 'touch /app/backend/internal/templates/text_autofit.go "
        "/app/backend/internal/render/raster.go && sleep 8 && "
        "grep -c automationSingleLineLetterFit /app/backend/internal/render/raster.go'"
    )
    _, o, _ = c.exec_command(touch, timeout=60)
    print("hycanvas air touch:", o.read().decode().strip())
    _, o, _ = c.exec_command(
        f"docker logs {hc} 2>&1 | grep -E 'building|running|error' | tail -5",
        timeout=30,
    )
    print("hycanvas build log:", o.read().decode("utf-8", errors="replace").strip())
    time.sleep(6)
    _, o, _ = c.exec_command("curl -sS -m 15 http://127.0.0.1:5050/api/system/health", timeout=30)
    print("health:", o.read().decode().strip())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
