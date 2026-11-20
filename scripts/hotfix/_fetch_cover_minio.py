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
import os, asyncio, asyncpg, json
from pathlib import Path
from minio import Minio

ASSET="cca_03dd463fe4e04b5292c78b76a3771b3e"
SRC="cca_424cab51a47849d0bc628de9a5d2151f"

async def main():
    url=(os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://","postgresql://")
    conn=await asyncpg.connect(url)
    try:
        for aid in (ASSET, SRC):
            row=await conn.fetchrow("select * from content_cover_assets where id=$1", aid)
            d=dict(row)
            print("ASSET", aid, json.dumps({
                "bucket": d.get("bucket_name"),
                "object": d.get("object_name"),
                "w": d.get("image_width"),
                "h": d.get("image_height"),
                "size": d.get("file_size"),
                "ctype": d.get("content_type"),
                "meta": d.get("metadata_json"),
            }, ensure_ascii=False, default=str))
            endpoint=os.environ.get("MINIO_ENDPOINT") or os.environ.get("S3_ENDPOINT") or "minio:9000"
            access=os.environ.get("MINIO_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or "minioadmin"
            secret=os.environ.get("MINIO_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "minioadmin"
            secure=str(os.environ.get("MINIO_SECURE") or "false").lower() in {"1","true","yes"}
            # also try common env names in this project
            for k,v in os.environ.items():
                if "MINIO" in k or "S3" in k or "ACCESS" in k:
                    if "SECRET" in k or "PASSWORD" in k or "KEY" in k:
                        print("ENV", k, "***")
                    else:
                        print("ENV", k, v)
            client=Minio(endpoint.replace("http://","").replace("https://",""), access_key=access, secret_key=secret, secure=secure)
            bucket=d["bucket_name"]; obj=d["object_name"]
            resp=client.get_object(bucket, obj)
            data=resp.read(); resp.close(); resp.release_conn()
            path=Path(f"/tmp/{aid}.bin")
            path.write_bytes(data)
            print("SAVED", aid, len(data), path)
    finally:
        await conn.close()

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
        with sftp.file("/tmp/_fetch_cover_minio.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_fetch_cover_minio.py api-dev:/tmp/_fetch_cover_minio.py && "
            "timeout 90 docker exec -w /app api-dev python /tmp/_fetch_cover_minio.py",
        )
        # pull cover asset
        try:
            sftp = c.open_sftp()
            # find which file exists
            listing = run(c, "ls -la /tmp/cca_*.bin 2>/dev/null || true")
            out += "\n" + listing
            sftp.get("/tmp/cca_03dd463fe4e04b5292c78b76a3771b3e.bin", str(LOCAL_IMG))
            sftp.close()
            out += f"\nLOCAL {LOCAL_IMG} {LOCAL_IMG.stat().st_size}\n"
        except Exception as e:
            out += f"\nPULL_FAIL {e}\n"
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:9000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
