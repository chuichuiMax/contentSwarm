#!/usr/bin/env python3
import os, time, paramiko
HOST="172.16.103.15"
PASSWORD=os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(HOST, username="root", password=PASSWORD, timeout=20)

def run(cmd, timeout=30):
    chan=c.get_transport().open_session(); chan.settimeout(timeout); chan.exec_command(cmd)
    chunks=[]; deadline=time.time()+timeout
    while time.time()<deadline:
        if chan.recv_ready(): chunks.append(chan.recv(65535))
        if chan.recv_stderr_ready(): chunks.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready(): chunks.append(chan.recv(65535))
            while chan.recv_stderr_ready(): chunks.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    else:
        chan.close(); raise TimeoutError(cmd)
    return b"".join(chunks).decode("utf-8","replace")

print(run("docker images | grep -i playwright || true; ps aux | grep -E 'docker pull|playwright' | grep -v grep || true; ls -la /tmp/_nav* 2>/dev/null || true"))
c.close()
