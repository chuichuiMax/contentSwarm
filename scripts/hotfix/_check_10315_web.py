import os
import sys

import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
cmds = [
    "grep -n getItemThumbnail /srv/contentswarm/web/src/views/ContentStudioView.vue | head -5",
    "grep -n file_url /srv/contentswarm/web/src/views/ContentStudioView.vue | head -5",
    "docker logs api-dev --tail 50 2>&1 | grep 'material-library/items' | tail -10",
]
for cmd in cmds:
    _, o, _ = c.exec_command(cmd, timeout=60)
    print("===", cmd)
    print(o.read().decode("utf-8", "replace"))
c.close()
