#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
LOCAL_COVER = Path(__file__).resolve().with_suffix(".cover.png")
LOCAL_SRC = Path(__file__).resolve().with_suffix(".src.bin")
LOCAL_TPL = Path(__file__).resolve().with_suffix(".tpl.png")

REMOTE = r'''
import asyncio, io, json, os
from pathlib import Path
from PIL import Image, ImageStat
from sqlalchemy import select

ASSET="cca_03dd463fe4e04b5292c78b76a3771b3e"
SRC="cca_424cab51a47849d0bc628de9a5d2151f"
TEMPLATE="baa7b9b9-effc-4a98-a6c3-f1e790fe34cc"
DESIGN="2769a923-4297-4577-bd19-06455d95980b"

async def main():
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.storage.postgres.models_content import ContentCoverAsset
    from yuxi.services.material_upload_queue import read_material_bytes_or_thumbnail

    async with pg_manager.get_async_session_context() as db:
        for aid, out in ((ASSET, "/tmp/cover_out.png"), (SRC, "/tmp/cover_src.bin")):
            row = (await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.id == aid))).scalar_one()
            data, ctype = await read_material_bytes_or_thumbnail(row)
            Path(out).write_bytes(data)
            im = Image.open(io.BytesIO(data)); im.load()
            print("IMG", aid, im.size, im.mode, ctype, len(data))
            rgb = im.convert("RGB")
            for name, box in (
                ("top", (0,0,im.width,80)),
                ("mid", (0, im.height//2-40, im.width, im.height//2+40)),
                ("bottom", (0, im.height-80, im.width, im.height)),
            ):
                crop = rgb.crop(box)
                px = list(crop.getdata())
                bright = sum(1 for r,g,b in px if r+g+b > 600)
                print(name, "mean", [round(x,1) for x in ImageStat.Stat(crop).mean], "bright", bright, "/", len(px))
            # save bottom strip for inspection
            rgb.crop((0, im.height-120, im.width, im.height)).save(f"/tmp/{aid}_bottom.png")

    # call hycanvas HTTP directly
    import httpx
    base = os.environ.get("HYCANVAS_BACKEND_URL") or os.environ.get("HYCANVAS_API_URL") or "http://hycanvas-app:8005"
    print("HYCANVAS_BASE", base)
    async with httpx.AsyncClient(timeout=60.0) as client:
        for label, path in (
            ("tpl", f"/api/v1/templates/{TEMPLATE}"),
            ("tpl_render", f"/api/v1/templates/{TEMPLATE}/render.png"),
            ("design_render", f"/api/v1/designs/{DESIGN}/render.png"),
        ):
            try:
                r = await client.get(base + path)
                print(label, r.status_code, r.headers.get("content-type"), len(r.content))
                if label == "tpl" and r.status_code == 200:
                    print("TPL_JSON", r.text[:3500])
                if "png" in (r.headers.get("content-type") or "") or r.content[:8].startswith(b"\x89PNG"):
                    Path(f"/tmp/{label}.png").write_bytes(r.content)
                    im = Image.open(io.BytesIO(r.content)); im.load()
                    print(label, "size", im.size)
                    bottom = im.convert("RGB").crop((0, im.height-80, im.width, im.height))
                    px = list(bottom.getdata())
                    bright = sum(1 for r0,g,b in px if r0+g+b > 600)
                    print(label, "bottom_bright", bright, "/", len(px))
            except Exception as e:
                print(label, "FAIL", type(e).__name__, e)

asyncio.run(main())
'''


def run(c, cmd, timeout=180):
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
        with sftp.file("/tmp/_fetch_cover_simple.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_fetch_cover_simple.py api-dev:/tmp/_fetch_cover_simple.py && "
            "timeout 150 docker exec -w /app api-dev python /tmp/_fetch_cover_simple.py",
            timeout=180,
        )
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/cover_out.png", LOCAL_COVER),
            ("/tmp/cover_src.bin", LOCAL_SRC),
            ("/tmp/tpl_render.png", LOCAL_TPL),
            ("/tmp/design_render.png", Path(__file__).resolve().with_suffix(".design.png")),
            ("/tmp/cca_03dd463fe4e04b5292c78b76a3771b3e_bottom.png", Path(__file__).resolve().with_suffix(".bottom.png")),
        ):
            try:
                sftp.get(remote, str(local))
                out += f"\nGOT {remote} -> {local.name} {local.stat().st_size}\n"
            except Exception as e:
                out += f"\nMISS {remote} {e}\n"
        sftp.close()
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:12000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
