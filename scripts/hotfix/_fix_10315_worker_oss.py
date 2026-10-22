"""103.15: sync worker OSS fix and recreate worker."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
REPO = Path(__file__).resolve().parents[2]
FILES = (
    "docker-compose.yml",
    "backend/server/worker_dev.py",
)

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)
sftp = c.open_sftp()
for rel in FILES:
    local = REPO / rel
    remote = f"{BASE}/{rel.replace(chr(92), '/')}"
    sftp.put(str(local), remote)
    print("uploaded", rel)
sftp.close()

cmds = [
    f"cd {BASE} && docker compose --env-file .env up -d --no-deps worker",
    "docker exec worker-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0'",
    "docker exec worker-dev python -c 'import alibabacloud_oss_v2; print(\"worker oss ok\")'",
]
for cmd in cmds:
    print(">>>", cmd)
    _, o, e = c.exec_command(cmd, timeout=300)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if out:
        print(out)
    if err.strip():
        print(err[:1200])
c.close()
