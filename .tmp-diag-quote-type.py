"""Inspect recent content generation errors on test for quote_type / cover."""

from __future__ import annotations

import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run(c, cmd, timeout=90):
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    chan = stdout.channel
    chan.settimeout(timeout)
    while not chan.exit_status_ready():
        time.sleep(0.3)
    text = (stdout.read().decode("utf-8", "replace") + "\n" + stderr.read().decode("utf-8", "replace")).strip()
    print(text[-16000:], flush=True)
    return chan.recv_exit_status()


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.16.103.15", username="root", password="Hirun2026@", timeout=20)
    run(
        c,
        r"""
set -eu
echo '=== recent errors ==='
docker logs worker-dev --since 45m 2>&1 | grep -Ei 'quote_type|标准物料|封面 Agent|副标题|缺少变量|MaterialGate|material_gate|ContentApplicationError' | tail -60
echo
echo '=== recent failed tasks ==='
docker exec postgres psql -U postgres -d yuxi -c "
SELECT t.id, t.status, left(coalesce(t.error_message,''),160) AS err,
       left(coalesce(t.content_type_code, t.requirement_json->>'contentType',''),40) AS ctype,
       t.updated_at
FROM content_tasks t
WHERE t.updated_at > now() - interval '2 hours'
ORDER BY t.updated_at DESC
LIMIT 15;
" 2>/dev/null || docker exec postgres psql -U postgres -d yuxi -c "\dt content_*" | head -40
""",
    )
    c.close()


if __name__ == "__main__":
    main()
