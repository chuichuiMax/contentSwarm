"""Install OSS SDK into test-server api/worker containers."""

from __future__ import annotations

import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.16.103.15", username="root", password="Hirun2026@", timeout=20)
    _, stdout, stderr = c.exec_command(
        r"""
set -eu
echo '==== STORAGE_BACKEND ===='
docker exec api-dev sh -c 'printenv STORAGE_BACKEND || true'
echo '==== install sdk ===='
docker exec api-dev python -m pip install 'alibabacloud-oss-v2>=1.1.0'
docker exec worker-dev python -m pip install 'alibabacloud-oss-v2>=1.1.0'
echo '==== import check ===='
docker exec api-dev python -c 'import alibabacloud_oss_v2 as oss; print("api ok", oss.__name__)'
docker exec worker-dev python -c 'import alibabacloud_oss_v2 as oss; print("worker ok", oss.__name__)'
""",
        timeout=180,
    )
    chan = stdout.channel
    chan.settimeout(180)
    while not chan.exit_status_ready():
        time.sleep(0.4)
    print(stdout.read().decode("utf-8", "replace")[-20000:])
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        print("STDERR:", err[-8000:])
    print("exit", chan.recv_exit_status())
    c.close()


if __name__ == "__main__":
    main()
