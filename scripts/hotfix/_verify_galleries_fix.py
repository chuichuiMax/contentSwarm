#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"

PROBE = r'''
import asyncio, traceback
from sqlalchemy import select, text
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.services.material_library_service import list_image_galleries

async def main():
    await pg_manager.initialize()
    async with pg_manager.get_session() as db:
        users = list((await db.execute(select(User).limit(30))).scalars())
        print("users", len(users))
        last = None
        for u in users:
            try:
                result = await list_image_galleries(db, u, industry_slug="decoration")
                print("OK", getattr(u, "username", None) or u.uid, "n=", len(result.get("galleries", [])))
                # show one gallery keys for audience fields
                if result.get("galleries"):
                    g = result["galleries"][0]
                    print("sample_keys", sorted([k for k in g if "audience" in k or k in ("id","name","visibility")])[:20])
                return
            except Exception as e:
                last = e
                print("FAIL", getattr(u, "username", None) or u.uid, type(e).__name__, str(e)[:200])
                await db.rollback()
        if last:
            traceback.print_exception(type(last), last, last.__traceback__)
        raise SystemExit(1)

asyncio.run(main())
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
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
        raise TimeoutError(cmd[:100])
    return b"".join(chunks).decode("utf-8", "replace").encode("ascii", "replace").decode("ascii")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=15, banner_timeout=15, auth_timeout=15)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_probe_galleries2.py", "w") as f:
            f.write(PROBE)
        sftp.close()
        print(
            run(
                c,
                "docker cp /tmp/_probe_galleries2.py api-dev:/tmp/_probe_galleries2.py && "
                "docker exec -w /app api-dev python /tmp/_probe_galleries2.py",
                timeout=120,
            )
        )
        print("=== live request log watcher tip ===")
        print(run(c, "curl -s -m 3 -o /dev/null -w 'health:%{http_code}\\n' http://127.0.0.1:5050/api/system/health"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
