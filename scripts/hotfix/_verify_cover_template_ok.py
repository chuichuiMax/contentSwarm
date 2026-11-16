#!/usr/bin/env python3
import os
import time
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "172.16.103.15",
    username="root",
    password=os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@",
    timeout=20,
)


def run(cmd: str, timeout: int = 60) -> str:
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks = []
    while True:
        if chan.recv_ready():
            chunks.append(chan.recv(65535))
        if chan.recv_stderr_ready():
            chunks.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready():
                chunks.append(chan.recv(65535))
            while chan.recv_stderr_ready():
                chunks.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    return b"".join(chunks).decode("utf-8", "replace").encode("ascii", "replace").decode("ascii")


print(run("curl -s -m 5 -o /dev/null -w 'health:%{http_code}\\n' http://127.0.0.1:5050/api/system/health"))
print(run("docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' api-dev"))
print(
    run(
        "docker logs api-dev --since 10m 2>&1 | grep 'material-library/items?material_type=cover_template' | tail -20"
    )
)
print(run("docker logs api-dev --since 5m 2>&1 | grep -E 'NameError|500|' | grep -E 'NameError|ERROR.*material' | tail -10 || true"))
c.close()
