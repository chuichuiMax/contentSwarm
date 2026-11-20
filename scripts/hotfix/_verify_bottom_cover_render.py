#!/usr/bin/env python3
"""Re-preview ct_e4788cac source with template to confirm bottom watermark survives."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
LOCAL_PNG = Path(__file__).resolve().with_suffix(".png")
LOCAL_BOTTOM = Path(__file__).resolve().with_suffix(".bottom.png")
SERVICE_PY = Path(__file__).resolve().parents[2] / "backend/package/yuxi/services/content_cover_service.py"

REMOTE = r'''
import asyncio, base64, io, json, os
from pathlib import Path
import httpx
from PIL import Image, ImageStat
from sqlalchemy import select

TEMPLATE = "baa7b9b9-effc-4a98-a6c3-f1e790fe34cc"
SRC = "cca_424cab51a47849d0bc628de9a5d2151f"

async def main():
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_content import ContentCoverAsset
    from yuxi.services.material_upload_queue import read_material_bytes_or_thumbnail

    async with pg_manager.get_async_session_context() as db:
        row = (await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.id == SRC))).scalar_one()
        data, ctype = await read_material_bytes_or_thumbnail(row)
    print("SRC", len(data), ctype)
    base = (os.getenv("HYCANVAS_BASE_URL") or "").rstrip("/")
    key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    headers = {"Authorization": f"Bearer {key}"}
    payload = {
        "backgroundImage": {
            "filename": "src.webp",
            "contentType": ctype or "image/webp",
            "dataBase64": base64.b64encode(data).decode("ascii"),
        }
    }
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=90.0) as client:
        # wait briefly for air rebuild if needed
        for i in range(8):
            r = await client.post(f"/api/v1/templates/{TEMPLATE}/preview.png", json=payload)
            print("PREVIEW", i, r.status_code, r.headers.get("content-type"), len(r.content))
            if r.status_code == 200 and r.content[:8].startswith(b"\x89PNG"):
                Path("/tmp/bottom_cover_preview.png").write_bytes(r.content)
                im = Image.open(io.BytesIO(r.content)); im.load()
                print("SIZE", im.size)
                bottom = im.convert("RGB").crop((0, im.height - 140, im.width, im.height))
                bottom.save("/tmp/bottom_cover_preview_bottom.png")
                px = list(bottom.getdata())
                bright = sum(1 for a,b,c in px if a+b+c > 600)
                print("BOTTOM_BRIGHT", bright, "/", len(px), "mean", [round(x,1) for x in ImageStat.Stat(bottom).mean])
                # Heuristic: full watermark letters are taller/brighter than half-cropped tops
                return
            await asyncio.sleep(2)
        print("FAIL preview")

asyncio.run(main())
'''


def run(c, cmd, timeout=200):
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
        sftp = c.open_sftp()
        with sftp.file("/tmp/_verify_bottom_cover_render.py", "w") as f:
            f.write(REMOTE)
        sftp.put(str(SERVICE_PY), "/tmp/content_cover_service.py")
        sftp.close()
        # sync processing_version into api-dev if mounted
        sync = run(
            c,
            "docker cp /tmp/content_cover_service.py api-dev:/tmp/content_cover_service.py; "
            "docker exec api-dev sh -c '"
            "for d in /app/package/yuxi/services /app/backend/package/yuxi/services; do "
            "  if [ -f $d/content_cover_service.py ]; then cp /tmp/content_cover_service.py $d/content_cover_service.py; echo SYNCED=$d; fi; "
            "done'; "
            "docker cp /tmp/_verify_bottom_cover_render.py api-dev:/tmp/_verify_bottom_cover_render.py && "
            "timeout 120 docker exec -w /app api-dev python /tmp/_verify_bottom_cover_render.py",
            timeout=180,
        )
        OUT.write_text(sync, encoding="utf-8")
        print(sync.encode("ascii", "backslashreplace").decode("ascii")[-5000:])
        run(
            c,
            "docker cp api-dev:/tmp/bottom_cover_preview.png /tmp/bottom_cover_preview.png; "
            "docker cp api-dev:/tmp/bottom_cover_preview_bottom.png /tmp/bottom_cover_preview_bottom.png",
            timeout=30,
        )
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/bottom_cover_preview.png", LOCAL_PNG),
            ("/tmp/bottom_cover_preview_bottom.png", LOCAL_BOTTOM),
        ):
            try:
                sftp.get(remote, str(local))
                print("GOT", local, local.stat().st_size)
            except Exception as e:
                print("MISS", remote, e)
        sftp.close()
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
