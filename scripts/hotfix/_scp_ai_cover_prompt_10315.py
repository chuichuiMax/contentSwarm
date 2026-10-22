import os
import sys
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)
rel = "backend/package/yuxi/content_cover/ai_cover_prompt.py"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
sftp = c.open_sftp()
sftp.put(str(REPO / rel), f"/srv/contentswarm/{rel}")
sftp.close()
_, o, _ = c.exec_command(
    "docker exec api-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0'",
    timeout=120,
)
print(o.read().decode())
c.close()
print("OK", rel)
