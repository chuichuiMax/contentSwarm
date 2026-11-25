"""Deploy requirement-list share UI + backend to production (172.16.103.17)."""
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
    "backend/package/yuxi/services/requirement_list_service.py",
    "backend/server/routers/requirement_list_router.py",
    "backend/package/yuxi/storage/postgres/models_content.py",
    "backend/package/yuxi/storage/postgres/manager.py",
    "web/src/views/RequirementListView.vue",
    "web/src/apis/requirement_list_api.js",
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
        Path(__file__).resolve().parent / "10317.password",
    ):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 900) -> str:
    _, o, e = client.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", errors="replace").strip()
    err = e.read().decode("utf-8", errors="replace").strip()
    if err and "web_build_ok" not in out and "build_ok" not in out:
        out = f"{out}\n[stderr]\n{err}".strip()
    return out


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD or create .deploy/10317.password", file=sys.stderr)
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
            run(c, f"mkdir -p '{remote_dir}'", timeout=60)
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
    for cmd in (build_cmd, "docker restart api-dev", "docker restart web-dev"):
        print(">>>", cmd[:100])
        out = run(c, cmd, timeout=900)
        if out:
            _safe_print(out[-3000:])

    time.sleep(12)
    verify = (
        f"grep -c submitter_name {BASE}/web/dist-live/assets/RequirementListView*.js; "
        f"grep -c openShare {BASE}/web/dist-live/assets/RequirementListView*.js; "
        "docker exec api-dev curl -fsS -m 8 http://127.0.0.1:5050/api/system/health"
    )
    print(run(c, verify, timeout=60))

    db_check = (
        "docker exec api-dev python -c \""
        "import asyncio; from sqlalchemy import text; "
        "from yuxi.storage.postgres.database import engine; "
        "async def q():\\n"
        "  async with engine.connect() as c:\\n"
        "    r=await c.execute(text(\\\"SELECT to_regclass('public.requirement_list_submission_shares')\\\"));\\n"
        "    print('shares_table', r.scalar());\\n"
        "    r2=await c.execute(text(\\\"SELECT column_name FROM information_schema.columns "
        "WHERE table_name='requirement_list_submissions' AND column_name='submitter_name'\\\"));\\n"
        "    print('submitter_col', r2.scalar());\\n"
        "asyncio.run(q())\""
    )
    print(run(c, db_check, timeout=60))
    c.close()
    print("done requirement-list share deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())