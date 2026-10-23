"""Sync tracked backend/web + compose from local repo to 103.15 test server."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
PREFIXES = (
    "backend/package/yuxi/",
    "backend/server/",
    "web/src/",
)
EXTRA_FILES = (
    "docker-compose.yml",
    "web/public/ai-cover-prompts/",
)


def _deploy_password() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for candidate in (
        REPO / ".deploy" / "10315.password",
        REPO / ".deploy" / "password",
        Path(__file__).resolve().parent / "10315.password",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return ""


def _tracked_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        text=False,
    )
    paths: list[str] = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace").replace("\\", "/")
        if any(rel.startswith(p) for p in PREFIXES):
            paths.append(rel)
            continue
        if rel in EXTRA_FILES:
            paths.append(rel)
            continue
        if rel.startswith("web/public/ai-cover-prompts/"):
            paths.append(rel)
    return sorted(set(paths))


def main() -> int:
    pw = _deploy_password()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or .deploy/10315.password", file=sys.stderr)
        return 1

    files = _tracked_files()
    print(f"sync {len(files)} files -> {HOST}:{BASE}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=30)
    sftp = client.open_sftp()

    uploaded = 0
    for rel in files:
        src = REPO / rel
        if not src.is_file():
            continue
        remote = f"{BASE}/{PurePosixPath(rel)}"
        remote_dir = str(PurePosixPath(remote).parent)
        try:
            sftp.stat(remote_dir)
        except OSError:
            client.exec_command(f"mkdir -p '{remote_dir}'", timeout=30)
        sftp.put(str(src), remote)
        uploaded += 1
        if uploaded <= 5 or uploaded % 200 == 0:
            print(f"  ... {uploaded} ({rel})")
    sftp.close()
    print(f"uploaded {uploaded} files")

    cmds = [
        f"cd {BASE} && docker compose --env-file .env up -d --no-deps api web worker",
        "docker exec api-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0' 2>/dev/null || true",
        "docker exec worker-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0' 2>/dev/null || true",
    ]
    for cmd in cmds:
        print(">>>", cmd[:100])
        _, o, e = client.exec_command(cmd, timeout=300)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out.strip())
        if err.strip() and "Running" not in err:
            print(err.strip()[:500])

    time.sleep(6)
    verify = (
        "docker exec api-dev python -c \""
        "from yuxi.content_cover.ai_cover_prompt import format_case_share_title; "
        "print(format_case_share_title(form_values={'楼盘信息':'测试盘','外框面积':'120','设计风格':'现代'})); "
        "import alibabacloud_oss_v2; print('ok')\""
    )
    _, o, _ = client.exec_command(verify, timeout=90)
    print(o.read().decode("utf-8", "replace").strip())
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
