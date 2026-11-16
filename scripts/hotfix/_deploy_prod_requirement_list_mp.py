"""Deploy requirement-list MP API (public submit + employee records) to production (172.16.103.17)."""
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
    "backend/server/routers/mp_router.py",
    "backend/server/routers/requirement_list_router.py",
    "backend/server/routers/__init__.py",
    "backend/package/yuxi/storage/postgres/models_content.py",
    "backend/package/yuxi/storage/postgres/manager.py",
)


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
    if err:
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

    print(">>> restart api")
    print(run(c, "docker restart api-dev", timeout=120))
    time.sleep(15)

    verify = (
        "docker exec api-dev python -c \""
        "import urllib.request; "
        "spec=urllib.request.urlopen('http://127.0.0.1:5050/openapi.json', timeout=10).read().decode(); "
        "print('mp_public', '/api/mp/requirement-lists/public' in spec); "
        "print('pc_public', '/api/requirement-lists/public' in spec)\""
    )
    print(run(c, verify, timeout=60))

    health = run(c, "docker exec api-dev curl -fsS -m 8 http://127.0.0.1:5050/api/system/health", timeout=30)
    print("health", health)

    c.close()
    print("done production deploy (requirement-list)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
