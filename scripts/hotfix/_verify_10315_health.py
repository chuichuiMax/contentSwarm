#!/usr/bin/env python3
import os
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "172.16.103.15",
    username="root",
    password=os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@",
    timeout=20,
)
cmd = (
    "curl -s -o /dev/null -w 'api:%{http_code}\\n' http://127.0.0.1:5050/api/system/health; "
    "curl -s -o /dev/null -w 'web8080:%{http_code}\\n' http://127.0.0.1:8080/; "
    "docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' api-dev; "
    "grep -n material_audience /srv/contentswarm/backend/server/routers/__init__.py || echo init_clean; "
    "test -f /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py && echo stub_ok"
)
_, o, e = c.exec_command(cmd, timeout=60)
print(o.read().decode("utf-8", "replace"))
err = e.read().decode("utf-8", "replace")
if err.strip():
    print(err)
c.close()
