import os
from pathlib import Path
import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD") or Path(__file__).resolve().parents[2].joinpath(".deploy/10315.password").read_text().strip()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
_, o, _ = c.exec_command(
    "docker logs api-dev --tail 800 2>&1 | grep -A25 'ct_a020bad' | tail -80",
    timeout=90,
)
print(o.read().decode("utf-8", "replace"))
_, o, _ = c.exec_command(
    "docker logs api-dev --tail 800 2>&1 | grep -B2 -A20 'target_audience_tag' | tail -60",
    timeout=90,
)
print("--- tags ---")
print(o.read().decode("utf-8", "replace"))
c.close()
