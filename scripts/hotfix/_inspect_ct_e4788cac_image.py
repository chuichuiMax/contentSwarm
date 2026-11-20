#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
LOCAL_IMG = Path(__file__).resolve().with_suffix(".png")

REMOTE = r'''
import os, asyncio, asyncpg, json, urllib.request
from pathlib import Path

TASK = "ct_e4788cac9ba5461f8ccbc284cdd0d802"
ASSET = "cca_03dd463fe4e04b5292c78b76a3771b3e"
DESIGN = "2769a923-4297-4577-bd19-06455d95980b"

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        cols = [r["column_name"] for r in await conn.fetch(
            "select column_name from information_schema.columns where table_name='content_cover_assets'"
        )]
        print("ASSET_COLS", cols)
        row = await conn.fetchrow("select * from content_cover_assets where id=$1", ASSET)
        d = dict(row) if row else {}
        keep = {k: d.get(k) for k in (
            "id","kind","mime_type","width","height","byte_size","storage_key","public_url","url",
            "object_key","bucket","path","source","status","meta_json","metadata"
        ) if k in d}
        print("ASSET", json.dumps(keep, ensure_ascii=False, default=str)[:3000])

        job = await conn.fetchrow("select request_json, result_json from content_cover_jobs where id=$1",
                                  "ccj_f3f9fb3f95a24c18816c87f3d3957a62")
        if job:
            req = job["request_json"]; res = job["result_json"]
            if isinstance(req, str):
                req = json.loads(req)
            if isinstance(res, str):
                res = json.loads(res)
            print("REQ", json.dumps(req, ensure_ascii=False)[:2000])
            print("RES", json.dumps(res, ensure_ascii=False)[:2000])
    finally:
        await conn.close()

    # try download via local API paths
    candidates = [
        f"http://127.0.0.1:5050/api/content/covers/assets/{ASSET}/file",
        f"http://127.0.0.1:5050/api/content/covers/assets/{ASSET}",
        f"http://127.0.0.1:5050/api/content/covers/hycanvas/designs/{DESIGN}/render.png",
        f"http://127.0.0.1:8080/api/content/covers/hycanvas/designs/{DESIGN}/render.png",
    ]
    for u in candidates:
        try:
            with urllib.request.urlopen(u, timeout=30) as r:
                data = r.read()
                ctype = r.headers.get("content-type")
                print("FETCH", u, r.status, ctype, len(data))
                if data[:8].startswith(b"\x89PNG") or data[:3] == b"\xff\xd8\xff" or b"PNG" in data[:20]:
                    Path("/tmp/_ct_e4788cac_cover.bin").write_bytes(data)
                    print("SAVED", len(data))
                    break
                print("HEAD", data[:120])
        except Exception as e:
            print("FAIL", u, e)

asyncio.run(main())
'''


def run(c, cmd, timeout=120):
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
        with sftp.file("/tmp/_inspect_cover_img.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_cover_img.py api-dev:/tmp/_inspect_cover_img.py && "
            "timeout 90 docker exec -w /app api-dev python /tmp/_inspect_cover_img.py",
        )
        # pull saved image if present
        try:
            sftp = c.open_sftp()
            sftp.get("/tmp/_ct_e4788cac_cover.bin", str(LOCAL_IMG))
            sftp.close()
            out += f"\nLOCAL_IMG {LOCAL_IMG} size={LOCAL_IMG.stat().st_size}\n"
        except Exception as e:
            out += f"\nNO_LOCAL_IMG {e}\n"
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:9000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
