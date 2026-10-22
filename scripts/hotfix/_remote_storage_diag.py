"""Diagnose material storage on 103.15 without admin password."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = "172.16.103.15"
ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)

script = f"""set -e
grep -E '^STORAGE_BACKEND=' /srv/contentswarm/.env || true
docker exec api-dev printenv STORAGE_BACKEND || true
docker logs api-dev --tail 30 2>&1 | grep -E 'material-library|MATERIAL_|StorageError' | tail -15 || true
docker exec postgres psql -U postgres -d yuxi -t -A -F'|' -c "select i.id, i.asset_id, a.bucket_name, left(a.object_name,80), coalesce(a.metadata_json->>'ingest_status','') from content_material_library_items i join content_cover_assets a on a.id=i.asset_id where i.id='{ITEM}' limit 1;"
"""

REMOTE = "/tmp/_storage_diag.sh"
sftp = c.open_sftp()
with sftp.file(REMOTE, "w") as f:
    f.write(script)
sftp.close()

_, o, e = c.exec_command(f"bash {REMOTE}", timeout=120)
out = o.read().decode("utf-8", "replace")
err = e.read().decode("utf-8", "replace")
sys.stdout.buffer.write(out.encode("utf-8", "replace"))
if err.strip():
    sys.stdout.buffer.write(err.encode("utf-8", "replace"))

# object fetch inside api container
py = f"""
import asyncio
from sqlalchemy import select
from yuxi.db.models.content_cover import ContentCoverAsset
from yuxi.db.pg_manager import pg_manager
from yuxi.storage.minio.client import get_minio_client, reset_storage_client
from yuxi.services.material_upload_queue import material_thumb_object_name
import os

async def main():
    reset_storage_client()
    print('STORAGE_BACKEND', os.getenv('STORAGE_BACKEND'))
    client = get_minio_client()
    print('client', type(client).__name__)
    async with pg_manager.get_async_session_context() as db:
        row = await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.id == (
            select __import__('sqlalchemy').literal_column("asset_id")  # noqa
        )))
    # simpler: raw sql via sync
asyncio.run(main())
"""
# use heredoc on remote for asset download test
asset_script = r"""docker exec api-dev python - <<'PY'
import asyncio
import os
from sqlalchemy import text
from yuxi.db.pg_manager import pg_manager
from yuxi.storage.minio.client import get_minio_client, reset_storage_client
from yuxi.services.material_upload_queue import material_thumb_object_name, read_material_bytes

ITEM = """ + repr(ITEM) + r"""

async def main():
    reset_storage_client()
    print("STORAGE_BACKEND", os.getenv("STORAGE_BACKEND"))
    client = get_minio_client()
    print("client", type(client).__name__)
    async with pg_manager.get_async_session_context() as db:
        r = await db.execute(
            text(
                "select i.asset_id, a.bucket_name, a.object_name, a.metadata_json->>'ingest_status' "
                "from content_material_library_items i join content_cover_assets a on a.id=i.asset_id "
                "where i.id=:id limit 1"
            ),
            {"id": ITEM},
        )
        row = r.one_or_none()
    if not row:
        print("no db row")
        return
    asset_id, bucket, obj, ingest = row
    print("asset", asset_id, bucket, obj[:90], ingest)
    thumb = material_thumb_object_name(obj)
    for name in (thumb, obj):
        try:
            data = await client.adownload_file(bucket, name)
            print("OK", name[:80], len(data))
        except Exception as exc:
            print("FAIL", name[:80], type(exc).__name__, exc)

asyncio.run(main())
PY"""
_, o2, e2 = c.exec_command(asset_script, timeout=120)
out2 = o2.read().decode("utf-8", "replace")
sys.stdout.buffer.write(out2.encode("utf-8", "replace"))
c.close()
