"""Sync employee/traffic/material-search features to 103.15 test server."""
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
    "backend/package/yuxi/repositories/material_library_repository.py",
    "backend/package/yuxi/repositories/content_repository.py",
    "backend/package/yuxi/services/employee_service.py",
    "backend/server/routers/employee_router.py",
    "web/src/views/EmployeeManageView.vue",
    "web/src/views/MaterialLibraryView.vue",
    "web/src/views/TrafficStatsView.vue",
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


def main() -> int:
    pw = _deploy_password()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or create .deploy/10315.password (gitignored)", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=pw, timeout=30)
    sftp = client.open_sftp()
    for rel in FILES:
        src = REPO / rel
        remote = f"{BASE}/{PurePosixPath(rel)}"
        remote_dir = str(PurePosixPath(remote).parent)
        try:
            sftp.stat(remote_dir)
        except OSError:
            client.exec_command(f"mkdir -p '{remote_dir}'", timeout=30)
        sftp.put(str(src), remote)
        print(f"  uploaded {rel}")
    sftp.close()

    build_cmd = (
        f"set -e; cd {BASE}/web; "
        "docker run --rm -v /srv/contentswarm/web:/web -w /web node:22-alpine "
        "sh -c 'corepack enable && pnpm run build'; "
        "rm -rf dist-live.bak; cp -a dist-live dist-live.bak 2>/dev/null || true; "
        "mkdir -p dist-live; rm -rf dist-live/*; cp -a dist/. dist-live/; echo build_ok"
    )
    web_recreate = (
        f"cd {BASE} && docker compose -f docker-compose.yml -f docker-compose.live-capacity.yml "
        "up -d --no-deps --force-recreate web 2>/dev/null || docker restart web-dev"
    )
    for cmd in (build_cmd, "docker restart api-dev", web_recreate):
        print(">>>", cmd[:120])
        _, stdout, stderr = client.exec_command(cmd, timeout=600)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if out.strip():
            print(out.strip()[-2000:])
        if err.strip() and "Restarting" not in err:
            print(err.strip()[:800], file=sys.stderr)

    time.sleep(6)
    verify_cmds = (
        f"grep -c count_case_shares_by_owner {BASE}/backend/package/yuxi/repositories/material_library_repository.py",
        f"grep -l case_shares {BASE}/web/dist-live/assets/TrafficStatsView*.js 2>/dev/null | head -1",
    )
    for cmd in verify_cmds:
        _, stdout, _ = client.exec_command(cmd, timeout=60)
        print(stdout.read().decode("utf-8", "replace").strip())
    client.close()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
