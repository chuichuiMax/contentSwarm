from __future__ import annotations

import os
import sys

import paramiko

HOST = "172.16.103.17"

PY = r"""
import os
from yuxi.services.ai_cover_prompt_catalog_service import _oss_object_name_candidates, _build_ai_cover_oss_client
from yuxi.content_cover.ai_cover_prompt_catalog import spec_for_variant

print('bucket', os.getenv('AI_COVER_OSS_BUCKET'))
print('subdir', os.getenv('AI_COVER_OSS_SUBDIR'))
spec = spec_for_variant('p1')
client = _build_ai_cover_oss_client()
print('client', client is not None, getattr(client, 'bucket', None))
for name in _oss_object_name_candidates(spec)[:8]:
    ok = False
    if client:
        try:
            ok = client.file_exists('image', name.lstrip('/'))
        except Exception as e:
            ok = f'err:{e}'
    print(name, ok)
"""


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    cmd = "docker exec api-dev python -c " + repr(PY.strip())
    _, o, e = c.exec_command(cmd, timeout=120)
    print(o.read().decode("utf-8", errors="replace"))
    err = e.read().decode()
    if err.strip():
        print("stderr:", err[:500])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
