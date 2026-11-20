#!/usr/bin/env python3
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

REMOTE = r'''
import asyncio, base64, io, os
from pathlib import Path
import httpx
from PIL import Image
from sqlalchemy import select

TEMPLATE = "baa7b9b9-effc-4a98-a6c3-f1e790fe34cc"
SRC = "cca_424cab51a47849d0bc628de9a5d2151f"
WS = os.getenv("HYCANVAS_WORKSPACE_ID")

async def main():
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_content import ContentCoverAsset
    from yuxi.services.material_upload_queue import read_material_bytes_or_thumbnail
    async with pg_manager.get_async_session_context() as db:
        row = (await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.id == SRC))).scalar_one()
        data, ctype = await read_material_bytes_or_thumbnail(row)
    base = (os.getenv("HYCANVAS_BASE_URL") or "").rstrip("/")
    key = (os.getenv("HYCANVAS_API_KEY") or "").strip()
    headers = {"Authorization": f"Bearer {key}"}
    body = {
        "workspaceId": WS,
        "title": "bottom-focal-verify",
        "fields": {"field_1": "复合写意", "field_2": "236㎡", "field_3": "洋湖天序"},
        "images": {},
        "backgroundImage": {
            "filename": "src.webp",
            "contentType": ctype or "image/webp",
            "dataBase64": base64.b64encode(data).decode("ascii"),
        },
    }
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120.0) as client:
        r = await client.post(f"/api/v1/templates/{TEMPLATE}/instantiate", json=body)
        print("INST", r.status_code, r.text[:500])
        r.raise_for_status()
        design_id = r.json()["designId"]
        print("DESIGN", design_id)
        f = await client.get(f"/api/v1/designs/{design_id}/file")
        f.raise_for_status()
        bg = f.json()["pages"][0]["children"][0]
        print("FOCAL", bg.get("focalPoint"), "NAT", bg.get("source"))
        png = await client.get(f"/api/v1/designs/{design_id}/render.png")
        png.raise_for_status()
        Path("/tmp/bottom_focal_full.png").write_bytes(png.content)
        im = Image.open(io.BytesIO(png.content)); im.load()
        print("SIZE", im.size)
        im.crop((0, im.height - 160, im.width, im.height)).save("/tmp/bottom_focal_full_bottom.png")

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
        with sftp.file("/tmp/_verify_bottom_cover_full.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_verify_bottom_cover_full.py api-dev:/tmp/_verify_bottom_cover_full.py && "
            "timeout 150 docker exec -w /app api-dev python /tmp/_verify_bottom_cover_full.py",
            timeout=180,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii"))
        run(
            c,
            "docker cp api-dev:/tmp/bottom_focal_full.png /tmp/bottom_focal_full.png; "
            "docker cp api-dev:/tmp/bottom_focal_full_bottom.png /tmp/bottom_focal_full_bottom.png",
            timeout=30,
        )
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/bottom_focal_full.png", LOCAL_PNG),
            ("/tmp/bottom_focal_full_bottom.png", LOCAL_BOTTOM),
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
