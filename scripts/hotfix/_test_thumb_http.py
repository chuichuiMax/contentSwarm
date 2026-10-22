"""Call thumbnail API via docker internal + simulate HTTP status without login."""
from __future__ import annotations

import os
import sys

import paramiko

ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"
pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

# unauthenticated public curl via nginx
for url in (
    f"http://127.0.0.1:8080/api/material-library/items/{ITEM}/file",
    f"http://127.0.0.1:8080/api/material-library/items/{ITEM}/thumbnail",
    f"http://127.0.0.1:5050/api/material-library/items/{ITEM}/thumbnail",
):
    cmd = f"curl -s -o /dev/null -w '%{{http_code}}' {url}"
    _, o, _ = c.exec_command(cmd, timeout=60)
    print(url, o.read().decode().strip())

# in-process service call (no HTTP auth)
py = r"""docker exec api-dev python - <<'PY'
import asyncio
from yuxi.storage.postgres.manager import pg_manager
from yuxi.services.material_library_service import get_material_thumbnail
from yuxi.storage.postgres.models_business import User
from sqlalchemy import select, text

ITEM = """ + repr(ITEM) + r"""

async def main():
    async with pg_manager.get_async_session_context() as db:
        uid = (await db.execute(text("select owner_uid from content_material_library_items where id=:id"), {"id": ITEM})).scalar_one()
        user = (await db.execute(select(User).where(User.uid == uid))).scalar_one()
        data, name = await get_material_thumbnail(db, user, ITEM)
        print("service_ok", name, len(data))

asyncio.run(main())
PY"""
_, o, e = c.exec_command(py, timeout=120)
print(o.read().decode("utf-8", "replace"))
print(e.read().decode("utf-8", "replace")[:800])
c.close()
