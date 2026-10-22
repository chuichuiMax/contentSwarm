import os
import sys

import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)

item_id = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

cmds = [
    "grep -E '^STORAGE_BACKEND|^OSS_|^MINIO' /srv/contentswarm/.env | sed 's/SECRET=.*/SECRET=***/' | head -20",
    "docker exec api-dev python -c 'import alibabacloud_oss_v2; print(\"oss ok\")' 2>&1",
    "docker logs api-dev --tail 50 2>&1 | grep -iE 'material|thumbnail|oss|minio' | tail -15",
]
for cmd in cmds:
    print(">>>", cmd[:90])
    _, o, _ = c.exec_command(cmd, timeout=60)
    print(o.read().decode()[:2000])

c.close()
