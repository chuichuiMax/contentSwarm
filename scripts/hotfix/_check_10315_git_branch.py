"""Print git branch/commit on 172.16.103.15 /srv/contentswarm."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmd = f"""
set -e
echo '=== path ==='
ls -ld {BASE} || true
echo '=== git? ==='
if [ -d {BASE}/.git ]; then
  cd {BASE}
  echo BRANCH=$(git rev-parse --abbrev-ref HEAD)
  echo COMMIT=$(git rev-parse --short HEAD)
  echo SUBJECT=$(git log -1 --pretty=%s)
  echo DATE=$(git log -1 --pretty=%ci)
else
  echo 'NO_GIT_AT_/srv/contentswarm'
fi
echo '=== other repos ==='
for d in /srv/contentswarm /opt/contentswarm /root/contentswarm /home/*/contentswarm; do
  if [ -d "$d/.git" ]; then echo FOUND_GIT=$d; (cd "$d" && echo BRANCH=$(git rev-parse --abbrev-ref HEAD) COMMIT=$(git rev-parse --short HEAD)); fi
done
echo '=== deploy markers ==='
ls -la {BASE}/.deploy 2>/dev/null || true
ls -la {BASE}/VERSION* {BASE}/version* {BASE}/.git-commit* {BASE}/DEPLOY* 2>/dev/null || true
echo '=== recent files ==='
ls -lt {BASE} 2>/dev/null | head -n 12
echo '=== api health ==='
curl -sS -m 8 http://127.0.0.1:5050/api/system/health || true
echo
echo '=== compose project ==='
docker ps --format '{{{{.Names}}}} {{{{.Image}}}}' | head -n 20
"""
    _, o, e = c.exec_command(cmd, timeout=45)
    print(o.read().decode("utf-8", errors="replace").strip())
    err = e.read().decode("utf-8", errors="replace").strip()
    if err:
        print(err, file=sys.stderr)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
