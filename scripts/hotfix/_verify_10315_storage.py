import os
import sys

import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
script = r"""
set -e
echo STORAGE_BACKEND=$(docker exec api-dev printenv STORAGE_BACKEND)
docker exec api-dev python -c "from yuxi.storage.minio.client import get_minio_client; print('client', type(get_minio_client()).__name__)"
# login inside container using saves or env - skip if fails
docker logs api-dev --tail 8 2>&1 | tail -5
"""
_, o, e = c.exec_command(script, timeout=90)
print(o.read().decode())
print(e.read().decode())
c.close()
