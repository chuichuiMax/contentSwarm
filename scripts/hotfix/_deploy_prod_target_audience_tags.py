"""Deploy target-audience tag KB matching to production (172.16.103.17)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path, PurePosixPath

import paramiko

REPO = Path(__file__).resolve().parents[2]
HOST = "172.16.103.17"
BASE = "/opt/contentSwarm"
FILES = (
    "backend/package/yuxi/services/target_audience_tag_service.py",
    "backend/package/yuxi/content/mp_studio_direct.py",
    "backend/package/yuxi/services/content_service.py",
    "backend/server/routers/content_router.py",
    "web/src/views/ContentStudioView.vue",
    "web/src/apis/content_api.js",
    "web/src/utils/contentRequestPayload.js",
)


def _safe_print(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    for p in (
        REPO / ".deploy" / "10317.password",
        REPO / ".deploy" / "prod.password",
        REPO / ".deploy" / "10315.password",
        Path(__file__).resolve().parent / "10317.password",
    ):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or .deploy/10317.password", file=sys.stderr)
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

    build_cmd = (
        f"set -e; cd {BASE}/web; "
        f"docker run --rm -v {BASE}/web:/web -w /web node:22-alpine "
        "sh -c 'corepack enable && pnpm run build'; "
        "rm -rf dist-live.bak; cp -a dist-live dist-live.bak 2>/dev/null || true; "
        "mkdir -p dist-live; rm -rf dist-live/*; cp -a dist/. dist-live/; echo web_build_ok"
    )
    compose = f"cd {BASE} && docker compose --env-file .env up -d --no-deps api web worker"
    restart_api = "docker restart api-dev"
    for cmd in (build_cmd, compose, restart_api):
        print(">>>", cmd[:100])
        _, o, e = c.exec_command(cmd, timeout=900)
        out = o.read().decode("utf-8", errors="replace").strip()
        err = e.read().decode("utf-8", errors="replace").strip()
        if out:
            _safe_print(out[-2500:])
        if err and "web_build_ok" not in out:
            print(err[:800], file=sys.stderr)

    time.sleep(10)
    verify_py = (
        "docker exec api-dev python -c \""
        "from yuxi.services.target_audience_tag_service import "
        "GENERIC_TARGET_AUDIENCE_TAG_KB, list_target_audience_tags; "
        "import inspect; "
        "print('generic', GENERIC_TARGET_AUDIENCE_TAG_KB); "
        "print('fallback', 'GENERIC' in inspect.getsource(list_target_audience_tags))\""
    )
    _, o, e = c.exec_command(verify_py, timeout=60)
    print(o.read().decode("utf-8", errors="replace").strip())
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)

    _, o2, _ = c.exec_command(
        f"grep -l target-audience-tags {BASE}/web/dist-live/assets/*.js 2>/dev/null | head -1 || "
        f"grep -c refreshTargetAudienceTags {BASE}/web/src/views/ContentStudioView.vue",
        timeout=60,
    )
    print("web:", o2.read().decode().strip())
    c.close()
    print("done production deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
