import os
import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
_, o, _ = c.exec_command(
    "grep -E 'ADMIN|USERNAME|PASSWORD' /srv/contentswarm/.env | cut -d= -f1",
    timeout=30,
)
print(o.read().decode())
c.close()
