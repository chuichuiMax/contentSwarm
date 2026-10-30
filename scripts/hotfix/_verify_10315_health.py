import os
import time
from pathlib import Path
import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD") or Path(__file__).resolve().parents[2].joinpath(".deploy/10315.password").read_text().strip()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
time.sleep(5)
for cmd in [
    "curl -sS -m 20 http://127.0.0.1:5050/api/system/health",
    "curl -sS -m 10 -o /dev/null -w 'hycanvas=%{http_code}\\n' http://127.0.0.1:8005/healthz",
    "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-dev|hycanvas-app'",
]:
    _, o, e = c.exec_command(cmd, timeout=60)
    print((o.read() + e.read()).decode())
c.close()
