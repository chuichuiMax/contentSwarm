"""Sync target-audience tag KB feature to 103.15 test server."""
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
    "backend/package/yuxi/services/target_audience_tag_service.py",
    "backend/package/yuxi/content/mp_studio_direct.py",
    "backend/package/yuxi/services/content_service.py",
    "backend/server/routers/content_router.py",
    "web/src/views/ContentStudioView.vue",
    "web/src/apis/content_api.js",
    "web/src/utils/contentRequestPayload.js",
)


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for p in (REPO / ".deploy" / "10315.password", Path(__file__).resolve().parent / "10315.password"):
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
    c.exec_command("docker restart api-dev", timeout=120)
    time.sleep(6)
    verify = (
        "docker exec api-dev python -c \""
        "from yuxi.services.target_audience_tag_service import "
        "GENERIC_TARGET_AUDIENCE_TAG_KB, target_audience_tag_knowledge_base_name; "
        "print(target_audience_tag_knowledge_base_name('毛坯')); "
        "print(GENERIC_TARGET_AUDIENCE_TAG_KB)\""
    )
    _, o, e = c.exec_command(verify, timeout=60)
    print(o.read().decode("utf-8", errors="replace").strip())
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)
    _, o2, _ = c.exec_command(
        f"grep -c refreshTargetAudienceTags {BASE}/web/src/views/ContentStudioView.vue",
        timeout=30,
    )
    print("ContentStudioView hook:", o2.read().decode().strip())
    c.close()
    print("done — api-dev restarted; web-dev hot-reloads Vue sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
