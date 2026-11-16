#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"

SCRIPT = r'''
import os, urllib.request, urllib.error, asyncio, asyncpg
from yuxi.utils.auth_utils import AuthUtils

async def user_id():
    url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    print("dburl_prefix", (url or "")[:48])
    if not url:
        return None
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow("select id, username, role from users order by id asc limit 5")
        rows = await conn.fetch("select id, username, role from users order by id asc limit 5")
        for r in rows:
            print("user", dict(r))
        # prefer admin
        for r in rows:
            if str(r["role"]).lower() in {"admin", "superadmin"} or (r["username"] or "").lower() in {"admin", "superadmin"}:
                return r["id"]
        return rows[0]["id"] if rows else None
    finally:
        await conn.close()

uid = asyncio.run(user_id())
print("uid", uid)
token = AuthUtils.create_access_token({"sub": str(uid)})
url = "http://127.0.0.1:5050/api/material-library/galleries?industry_slug=decoration"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        print("OK", resp.status)
        print(body[:600].decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read()[:800].decode("utf-8", "replace"))
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
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
    return b"".join(chunks).decode("utf-8", "replace").encode("ascii", "replace").decode("ascii")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=15, banner_timeout=15, auth_timeout=15)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_galleries_jwt.py", "w") as f:
            f.write(SCRIPT)
        sftp.close()
        print(
            run(
                c,
                "docker cp /tmp/_galleries_jwt.py api-dev:/tmp/_galleries_jwt.py && "
                "timeout 40 docker exec -w /app api-dev python /tmp/_galleries_jwt.py",
                timeout=55,
            )
        )
        print("=== logs ===")
        print(run(c, "docker logs api-dev --since 2m 2>&1 | grep -E 'galleries|AttributeError| 500 ' | tail -20", timeout=20))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
