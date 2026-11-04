"""Deploy AI cover prompt materialize fix to 103.15 api-dev."""
from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
FILES = (
    "backend/package/yuxi/content_cover/ai_cover_prompt.py",
    "backend/package/yuxi/services/ai_cover_template_service.py",
    "backend/package/yuxi/services/content_cover_service.py",
    "backend/test/unit/content/test_ai_cover_prompt_materialize.py",
)


def _pw() -> str:
    v = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    return v or Path(__file__).resolve().parents[2].joinpath(".deploy", "10315.password").read_text(encoding="utf-8").strip()


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
    _, o, _ = c.exec_command(
        "docker exec api-dev uv run --no-sync pytest test/unit/content/test_ai_cover_prompt_materialize.py -q",
        timeout=120,
    )
    print(o.read().decode("utf-8", errors="replace"))
    c.exec_command("docker restart api-dev", timeout=120)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
