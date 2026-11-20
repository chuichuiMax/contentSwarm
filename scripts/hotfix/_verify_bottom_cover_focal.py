#!/usr/bin/env python3
"""Sync instantiate.go changes to remote hycanvas and run unit tests."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
FILES = [
    ROOT / "apps/hycanvas/backend/internal/templates/instantiate.go",
    ROOT / "apps/hycanvas/backend/internal/templates/templates_test.go",
]
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=240):
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
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
    else:
        chan.close()
        raise TimeoutError(cmd[:120])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        # locate hycanvas source + container
        locate = run(
            c,
            "docker ps --format '{{.Names}}' | grep -E 'hycanvas|canvas' || true; "
            "ls -d /root/*/apps/hycanvas/backend /data/*/apps/hycanvas/backend "
            "/opt/*/apps/hycanvas/backend /home/*/contentSwarm/apps/hycanvas/backend "
            "2>/dev/null | head -5; "
            "find /root /data /opt /home -maxdepth 5 -type d -name hycanvas 2>/dev/null | head -10",
            timeout=60,
        )
        print(locate)
        sftp = c.open_sftp()
        for path in FILES:
            remote = f"/tmp/{path.name}"
            sftp.put(str(path), remote)
            print("PUT", path.name, path.stat().st_size)
        sftp.close()

        # Prefer copying into a known deploy tree if present; also docker cp into container.
        out = run(
            c,
            r'''
set -e
CONT=$(docker ps --format '{{.Names}}' | grep -E 'hycanvas' | head -1)
echo CONT=$CONT
# find mounted backend path inside common compose layouts
for d in \
  /root/contentSwarm/apps/hycanvas/backend \
  /data/contentSwarm/apps/hycanvas/backend \
  /opt/contentSwarm/apps/hycanvas/backend \
  /home/*/contentSwarm/apps/hycanvas/backend \
  /root/*/apps/hycanvas/backend; do
  if [ -d "$d/internal/templates" ]; then
    echo DEST=$d
    cp /tmp/instantiate.go "$d/internal/templates/instantiate.go"
    cp /tmp/templates_test.go "$d/internal/templates/templates_test.go"
    break
  fi
done
if [ -n "$CONT" ]; then
  docker cp /tmp/instantiate.go "$CONT:/tmp/instantiate.go"
  docker cp /tmp/templates_test.go "$CONT:/tmp/templates_test.go"
  # if container has the source tree, copy into place
  docker exec "$CONT" sh -c '
    for d in /app /src /workspace /hycanvas /backend; do
      if [ -d "$d/internal/templates" ]; then
        cp /tmp/instantiate.go "$d/internal/templates/instantiate.go"
        cp /tmp/templates_test.go "$d/internal/templates/templates_test.go"
        echo UPDATED=$d
        cd "$d" && go test ./internal/templates/ -count=1 -run TestApplyBackgroundImage
        exit $?
      fi
    done
    # air/dev layout
    if [ -d /app/backend/internal/templates ]; then
      cp /tmp/instantiate.go /app/backend/internal/templates/instantiate.go
      cp /tmp/templates_test.go /app/backend/internal/templates/templates_test.go
      echo UPDATED=/app/backend
      cd /app/backend && go test ./internal/templates/ -count=1 -run TestApplyBackgroundImage
      exit $?
    fi
    echo "no templates dir in container"; ls /; exit 2
  '
else
  echo "no hycanvas container"
  exit 3
fi
''',
            timeout=240,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii"))
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
