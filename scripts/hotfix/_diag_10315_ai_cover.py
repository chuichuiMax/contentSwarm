"""Diagnose AI cover catalog + previews on 103.15."""
from __future__ import annotations

import json
import os
import sys

import paramiko

HOST = "172.16.103.15"
pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)

cmds = [
    "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-dev|web-dev'",
    "docker exec api-dev python -c 'import alibabacloud_oss_v2; print(\"oss_v2 ok\")' 2>&1",
    "grep -E '^AI_COVER_|^STORAGE_BACKEND|^OSS_ACCESS' /srv/contentswarm/.env | sed 's/SECRET=.*/SECRET=***/'",
    "python3 - <<'PY'\nimport json, urllib.request\nfor q in ['content_type_code=CT06', 'content_type_code=CT01', '']:\n  url=f'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts?{q}'\n  try:\n    with urllib.request.urlopen(url, timeout=15) as r:\n      d=json.load(r)\n    print(q or 'no_query', 'count', len(d.get('prompts',[])), 'variants', [p['variant'] for p in d.get('prompts',[])])\n  except Exception as e:\n    print(q, 'ERR', e)\nPY",
    "for v in p1 p2 p7 p13; do curl -s -o /dev/null -w \"$v %{http_code} %{size_download}\\n\" \"http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/$v/preview.png?content_type_code=CT06\"; done",
    "docker logs api-dev --tail 40 2>&1 | tail -25",
]
for cmd in cmds:
    print("\n>>>", cmd.split("\n")[0][:100])
    _, o, e = c.exec_command(cmd, timeout=120)
    out = o.read().decode()
    err = e.read().decode()
    if out.strip():
        print(out, end="" if out.endswith("\n") else "\n")
    if err.strip():
        print("stderr:", err.strip()[:1200])

c.close()
