"""Safe disk cleanup on 103.15: Docker prune (no volumes) + journal/log trim."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)

    script = r'''set -e
echo "=== BEFORE df ==="
df -h / /srv 2>/dev/null || df -h /
echo ""
echo "=== docker system df ==="
docker system df -v 2>/dev/null | head -40
echo ""
echo "=== largest docker dirs (top 12) ==="
du -sh /var/lib/docker/* 2>/dev/null | sort -hr | head -12 || true
echo ""
echo "=== prune: stopped containers, unused networks, dangling images ==="
docker container prune -f
docker network prune -f
docker image prune -f
echo ""
echo "=== prune: unused images (not referenced by any container) ==="
docker image prune -a -f
echo ""
echo "=== prune: build cache ==="
docker builder prune -af 2>/dev/null || docker builder prune -f
echo ""
echo "=== trim journal (keep 3 days) ==="
journalctl --vacuum-time=3d 2>/dev/null || true
echo ""
echo "=== truncate huge container logs (>50M) ==="
find /var/lib/docker/containers -name '*-json.log' -size +50M -print -exec truncate -s 20M {} \; 2>/dev/null || true
echo ""
echo "=== AFTER docker system df (summary) ==="
docker system df
echo ""
echo "=== AFTER df ==="
df -h / /srv 2>/dev/null || df -h /
'''
    path = "/tmp/_prune_disk.sh"
    sftp = c.open_sftp()
    with sftp.file(path, "w") as f:
        f.write(script)
    sftp.close()
    _, o, e = c.exec_command(f"bash {path}", timeout=600)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    code = o.channel.recv_exit_status()
    c.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
