import os
import sys
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "backend/package/yuxi/services/ai_cover_prompt_catalog_service.py"

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

sftp = c.open_sftp()
sftp.put(str(SERVICE), "/srv/contentswarm/backend/package/yuxi/services/ai_cover_prompt_catalog_service.py")
sftp.close()
print("synced ai_cover_prompt_catalog_service.py")

for cmd in (
    "docker exec api-dev uv pip install --system 'alibabacloud-oss-v2>=1.1.0'",
    "docker exec api-dev python -c 'import alibabacloud_oss_v2; print(\"oss_v2 ok\")'",
    "curl -s -o /tmp/p7.png -w 'p7 %{http_code} %{size_download}\\n' "
    "'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p7/preview.png?content_type_code=CT06'",
    "file /tmp/p7.png",
):
    print(">>>", cmd[:90])
    _, o, e = c.exec_command(cmd, timeout=300)
    out = o.read().decode()
    err = e.read().decode().strip()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print("stderr:", err[:800])

c.close()
