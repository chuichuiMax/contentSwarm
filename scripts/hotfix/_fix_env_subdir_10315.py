import os
import sys
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)

local_env = REPO / ".env"
lines = []
if local_env.is_file():
    lines = local_env.read_text(encoding="utf-8").splitlines()
else:
    lines = []

# Build minimal patch: read remote .env and fix one line
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
sftp = c.open_sftp()
with sftp.open("/srv/contentswarm/.env", "r") as f:
    raw = f.read().decode("utf-8", errors="replace")
out_lines = []
found = False
for line in raw.splitlines():
    if line.startswith("AI_COVER_OSS_SUBDIR="):
        out_lines.append("AI_COVER_OSS_SUBDIR=AI封面")
        found = True
    else:
        out_lines.append(line)
if not found:
    out_lines.append("AI_COVER_OSS_SUBDIR=AI封面")
fixed = "\n".join(out_lines) + "\n"
with sftp.open("/srv/contentswarm/.env", "w") as f:
    f.write(fixed.encode("utf-8"))
sftp.close()
_, o, _ = c.exec_command("grep AI_COVER_OSS_SUBDIR /srv/contentswarm/.env | od -An -tx1 | head -1", timeout=30)
print(o.read().decode())
c.exec_command("cd /srv/contentswarm && docker compose --env-file .env up -d --no-deps api", timeout=120)
c.close()
print("fixed .env UTF-8 subdir, api restarted")
