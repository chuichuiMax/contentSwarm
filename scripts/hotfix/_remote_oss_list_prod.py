from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.17"
BASE = "/opt/contentSwarm"
LOCAL = Path(__file__).resolve().parent / "_oss_probe_remote.py"

REMOTE_PY = """\
import os
from yuxi.services.ai_cover_prompt_catalog_service import (
    _build_ai_cover_oss_client,
    _oss_object_name_candidates,
)
from yuxi.content_cover.ai_cover_prompt_catalog import spec_for_variant

print("bucket", os.getenv("AI_COVER_OSS_BUCKET"))
print("subdir", repr(os.getenv("AI_COVER_OSS_SUBDIR")))
print("oss_bucket_env", os.getenv("OSS_BUCKET"))
client = _build_ai_cover_oss_client()
print("client", client is not None, getattr(client, "bucket", None) if client else None)
spec = spec_for_variant("p1")
for name in _oss_object_name_candidates(spec):
    ok = False
    if client:
        try:
            ok = client.file_exists("image", name.lstrip("/"))
        except Exception as exc:
            ok = f"err:{exc}"
    print("candidate", name, ok)
"""


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    LOCAL.write_text(REMOTE_PY, encoding="utf-8")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    remote = f"{BASE}/backend/package/yuxi/_oss_probe_remote.py"
    sftp.put(str(LOCAL), remote)
    sftp.close()
    _, o, e = c.exec_command("docker exec api-dev python /app/package/yuxi/_oss_probe_remote.py", timeout=120)
    print(o.read().decode("utf-8", errors="replace"))
    err = e.read().decode()
    if err.strip():
        print("stderr:", err[:800])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
