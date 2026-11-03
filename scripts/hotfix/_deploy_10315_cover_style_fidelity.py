"""Deploy cover PNG style fidelity (textEffects + letter-spacing guard) to 103.15."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
FILES = (
    "apps/hycanvas/backend/internal/render/text_effects_raster.go",
    "apps/hycanvas/backend/internal/render/raster.go",
    "apps/hycanvas/backend/internal/render/automation_text_autofit.go",
    "apps/hycanvas/backend/internal/templates/text_autofit.go",
)


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
    repo = Path(__file__).resolve().parents[2]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in FILES:
        sftp.put(str(repo / rel), f"{BASE}/{PurePosixPath(rel)}")
        print("OK", rel)
    sftp.close()
    hc = "contentswarm-hycanvas-app-1"
    c.exec_command(f"docker exec {hc} sh -c 'touch /app/backend/internal/render/raster.go /app/backend/internal/templates/text_autofit.go'", timeout=30)
    time.sleep(10)
    _, o, _ = c.exec_command(f"docker logs {hc} 2>&1 | grep -E 'running|error' | tail -4", timeout=30)
    print(o.read().decode("utf-8", errors="replace"))
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
