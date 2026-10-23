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
remote = f"/srv/contentswarm/{rel}"
sftp.put(str(REPO / rel), remote)
sftp.close()
checks = (
    "grep -q '缺少必填字段' /srv/contentswarm/backend/package/yuxi/content_cover/ai_cover_prompt.py "
    "&& echo OLD_CODE || echo P12_FIX_OK",
    "docker exec worker-dev python -c \""
    "from yuxi.content_cover.ai_cover_prompt import build_site_header_lines; "
    "print(build_site_header_lines(title='T', subtitle='S', form_values={}))\"",
)
for cmd in checks:
    _, o, e = c.exec_command(cmd, timeout=120)
    print(o.read().decode("utf-8", "replace").strip())
    err = e.read().decode("utf-8", "replace").strip()
    if err:
        print(err[:500])
c.close()
print("OK", rel)
