"""103.15: enable OSS storage backend + verify material thumbnail."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)

fix_env = r"""python3 - <<'PY'
import re
from pathlib import Path
p = Path("/srv/contentswarm/.env")
text = p.read_text(encoding="utf-8", errors="replace")
if re.search(r"^STORAGE_BACKEND=", text, flags=re.M):
    text = re.sub(r"^STORAGE_BACKEND=.*$", "STORAGE_BACKEND=oss", text, flags=re.M)
else:
    text = text.rstrip() + "\nSTORAGE_BACKEND=oss\n"
p.write_text(text, encoding="utf-8")
print("STORAGE_BACKEND=oss")
PY"""
_, o, _ = c.exec_command(fix_env, timeout=30)
print(o.read().decode())

_, o, _ = c.exec_command(
    f"cd {BASE} && docker compose --env-file .env up -d --no-deps api worker",
    timeout=300,
)
print(o.read().decode())
print("waiting api...")
time.sleep(25)

check = f"""docker exec api-dev python - <<'PY'
import os
print("STORAGE_BACKEND", os.getenv("STORAGE_BACKEND"))
from yuxi.storage.minio.client import get_minio_client
c = get_minio_client()
print("client", type(c).__name__)
PY"""
_, o, _ = c.exec_command(check, timeout=60)
print(o.read().decode())

# asset bucket for item
sql = f"""docker exec postgres psql -U postgres -d yuxi -t -c "select i.id, a.bucket_name, a.object_name, a.metadata_json->>'ingest_status' from content_material_library_items i join content_cover_assets a on a.id=i.asset_id where i.id='{ITEM}' limit 1;" """
_, o, _ = c.exec_command(sql, timeout=30)
print("db row:", o.read().decode().strip())

c.close()
