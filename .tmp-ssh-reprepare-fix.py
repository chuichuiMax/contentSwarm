import io
import os
import tarfile
import sys
import paramiko

HOST, USER, PASSWORD = "172.16.103.15", "root", "Hirun2026@"
REPO = r"c:\hirunAI\cowxiao\contentSwarm"
REL = "backend/package/yuxi/services/viral_asset_worker.py"

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    tar.add(os.path.join(REPO, REL.replace("/", os.sep)), arcname=REL)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=30)
sftp = c.open_sftp()
with sftp.file("/tmp/worker-fix.tar.gz", "wb") as f:
    f.write(buf.getvalue())
sftp.close()

def run(cmd, timeout=7200):
    print(f"$ {cmd}", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    t = (o.read() + e.read()).decode("utf-8", "replace")
    print(t[-15000:], flush=True)
    return o.channel.recv_exit_status()

run("tar -xzf /tmp/worker-fix.tar.gz -C /srv/contentswarm")
code = run("docker exec api-dev uv run python scripts/reprepare_viral_types.py --apply", timeout=7200)
c.close()
sys.exit(code)
