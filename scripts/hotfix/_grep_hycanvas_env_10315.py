import os
import paramiko
from pathlib import Path

pw = os.environ.get("DEPLOY_SSH_PASSWORD") or Path(__file__).resolve().parents[2].joinpath(".deploy/10315.password").read_text().strip()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
_, o, _ = c.exec_command("grep HYCANVAS /srv/contentswarm/.env", timeout=30)
print(o.read().decode())
c.close()
