"""Compare selected files on 172.16.103.15 with local repo (current branch)."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.15"
BASE = "/srv/contentswarm"

# Representative paths covering API, AI cover, HyCanvas, frontend.
FILES = (
    "backend/package/yuxi/content_cover/ai_cover_prompt.py",
    "backend/package/yuxi/services/ai_cover_template_service.py",
    "backend/package/yuxi/services/content_cover_service.py",
    "backend/package/yuxi/content/control/visual_template_fields.py",
    "apps/hycanvas/backend/internal/persistence/persistence.go",
    "apps/hycanvas/backend/internal/templates/text_autofit.go",
    "apps/hycanvas/frontend/src/components/editor/SaveAsTemplateDialog.tsx",
    "apps/hycanvas/frontend/src/components/dashboard/DashboardApp.tsx",
    "web/src/views/HycanvasWorkspaceView.vue",
    "docker-compose.yml",
)


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    p = REPO / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1

    branch = subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    commit = subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], text=True
    ).strip()
    print(f"local branch={branch} commit={commit}")
    print(f"remote={HOST}:{BASE}")
    print()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()

    match = miss = absent = 0
    for rel in FILES:
        local = REPO / rel
        if not local.is_file():
            print(f"LOCAL_MISSING  {rel}")
            absent += 1
            continue
        local_hash = _sha256(local.read_bytes())
        remote = f"{BASE}/{PurePosixPath(rel)}"
        try:
            with sftp.open(remote, "rb") as f:
                remote_hash = _sha256(f.read())
        except OSError:
            print(f"REMOTE_ABSENT  {rel}")
            absent += 1
            continue
        if local_hash == remote_hash:
            print(f"MATCH          {rel}  {local_hash}")
            match += 1
        else:
            print(f"DIFF           {rel}  local={local_hash} remote={remote_hash}")
            miss += 1

    sftp.close()
    c.close()
    print()
    print(f"summary: match={match} diff={miss} missing={absent} total={len(FILES)}")
    if miss or absent:
        print("结论: 测试机并非完整对齐当前本地分支；至少部分文件不同或缺失。")
        return 2
    print("结论: 抽检文件与当前本地分支内容一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
