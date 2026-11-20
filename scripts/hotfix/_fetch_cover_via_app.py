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
LOCAL_RENDER = Path(__file__).resolve().with_suffix(".render.png")

REMOTE = r'''
import asyncio, io, json, os
from pathlib import Path
from PIL import Image, ImageStat

ASSET="cca_03dd463fe4e04b5292c78b76a3771b3e"
SRC="cca_424cab51a47849d0bc628de9a5d2151f"
DESIGN="2769a923-4297-4577-bd19-06455d95980b"
TEMPLATE="baa7b9b9-effc-4a98-a6c3-f1e790fe34cc"

async def main():
    from sqlalchemy import select
    from yuxi.storage.postgres.manager import pg_manager
    from yuxi.services.content_cover_worker import _download_asset
    from yuxi.storage.postgres.models_content import ContentCoverAsset
    from yuxi.services.hycanvas_service import HyCanvasClient

    async with pg_manager.get_async_session_context() as db:
        for aid, out in ((ASSET, "/tmp/cover_out.png"), (SRC, "/tmp/cover_src.bin")):
            row = (await db.execute(select(ContentCoverAsset).where(ContentCoverAsset.id == aid))).scalar_one()
            data = await _download_asset(row)
            Path(out).write_bytes(data)
            im = Image.open(io.BytesIO(data))
            im.load()
            print("IMG", aid, im.size, im.mode, len(data))
            bottom = im.convert("RGB").crop((0, im.height-60, im.width, im.height))
            top = im.convert("RGB").crop((0, 0, im.width, 60))
            print("EDGE", aid, "top", ImageStat.Stat(top).mean, "bottom", ImageStat.Stat(bottom).mean)
            px = list(bottom.getdata())
            bright = sum(1 for r,g,b in px if r+g+b > 600)
            dark = sum(1 for r,g,b in px if r+g+b < 120)
            print("BOTTOM_PX", aid, "bright", bright, "dark", dark, "total", len(px))

    client = HyCanvasClient.from_env()
    try:
        tpl = await client.get_xiaohongshu_template(TEMPLATE)
        print("TPL", json.dumps(tpl, ensure_ascii=False, default=str)[:3500])
    except Exception as e:
        print("TPL_FAIL", type(e).__name__, e)
    try:
        png, ctype = await client.render_png(DESIGN)
        Path("/tmp/cover_render.png").write_bytes(png)
        im = Image.open(io.BytesIO(png)); im.load()
        print("RENDER", im.size, ctype, len(png))
    except Exception as e:
        print("RENDER_FAIL", type(e).__name__, e)
    try:
        png, ctype = await client.render_template_png(TEMPLATE)
        Path("/tmp/cover_template_render.png").write_bytes(png)
        im = Image.open(io.BytesIO(png)); im.load()
        print("TPL_RENDER", im.size, ctype, len(png))
        bottom = im.convert("RGB").crop((0, im.height-60, im.width, im.height))
        px = list(bottom.getdata())
        bright = sum(1 for r,g,b in px if r+g+b > 600)
        print("TPL_BOTTOM_BRIGHT", bright, "total", len(px))
    except Exception as e:
        print("TPL_RENDER_FAIL", type(e).__name__, e)

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
        with sftp.file("/tmp/_fetch_cover_via_app.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_fetch_cover_via_app.py api-dev:/tmp/_fetch_cover_via_app.py && "
            "timeout 150 docker exec -w /app api-dev python /tmp/_fetch_cover_via_app.py",
            timeout=180,
        )
        sftp = c.open_sftp()
        for remote, local in (
            ("/tmp/cover_out.png", LOCAL_COVER),
            ("/tmp/cover_src.bin", LOCAL_SRC),
            ("/tmp/cover_render.png", LOCAL_RENDER),
            ("/tmp/cover_template_render.png", Path(__file__).resolve().with_suffix(".tpl.png")),
        ):
            try:
                sftp.get(remote, str(local))
                out += f"\nGOT {remote} -> {local} {local.stat().st_size}\n"
            except Exception as e:
                out += f"\nMISS {remote} {e}\n"
        sftp.close()
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:10000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
