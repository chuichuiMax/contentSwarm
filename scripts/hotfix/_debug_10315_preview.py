import os
import sys

import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

cmds = [
    "grep -E '^AI_COVER_|^OSS_ACCESS|^STORAGE_BACKEND' /srv/contentswarm/.env | sed 's/SECRET=.*/SECRET=***/'",
    "curl -s -o /dev/null -w 'direct5050 p7 %{http_code} %{size_download}\\n' 'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p7/preview.png?content_type_code=CT06'",
    "curl -s -o /dev/null -w 'vite8080 p7 %{http_code} %{size_download}\\n' 'http://127.0.0.1:8080/api/content/covers/ai-cover-prompts/p7/preview.png?content_type_code=CT06'",
    "curl -sI 'http://127.0.0.1:8080/api/content/covers/ai-cover-prompts/p7/preview.png?content_type_code=CT06' | tr -d '\\r' | head -8",
    "docker logs api-dev --tail 30 2>&1 | grep -i 'AI 封面' || docker logs api-dev --tail 15",
]
for cmd in cmds:
    print(">>>", cmd[:100])
    _, o, e = c.exec_command(cmd, timeout=120)
    out = o.read().decode()
    err = e.read().decode()
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err.strip():
        print("stderr:", err.strip())
c.close()
