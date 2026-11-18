#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = os.path.splitext(__file__)[0] + ".out.txt"

SCRIPT = r'''
import os, urllib.request, urllib.error, asyncio, asyncpg, traceback
from yuxi.utils.auth_utils import AuthUtils

async def admin_uid():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("select id, username, role from users order by id asc limit 20")
        for r in rows:
            print("user", dict(r))
        for r in rows:
            role = str(r["role"] or "").lower()
            name = str(r["username"] or "").lower()
            if role in {"admin", "superadmin"} or name in {"admin", "superadmin"}:
                return r["id"]
        return rows[0]["id"] if rows else None
    finally:
        await conn.close()

uid = asyncio.run(admin_uid())
print("uid", uid)
token = AuthUtils.create_access_token({"sub": str(uid)})
url = "http://127.0.0.1:5050/api/material-library/categories?material_type=image"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
        print("OK", resp.status, body[:500].decode("utf-8", "replace"))
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read()[:1200].decode("utf-8", "replace"))
except Exception:
    traceback.print_exc()
'''


def run(c, cmd, timeout=90):
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
        with sftp.file("/tmp/_diag_categories.py", "w") as f:
            f.write(SCRIPT)
        sftp.close()
        print(run(c, "docker cp /tmp/_diag_categories.py api-dev:/tmp/_diag_categories.py"))
        out = run(
            c,
            "timeout 70 docker exec -w /app api-dev python /tmp/_diag_categories.py",
            timeout=80,
        )
        logs = run(
            c,
            "docker logs api-dev --tail 120 2>&1 | tail -n 120",
            timeout=30,
        )
        text = out + "\n===logs===\n" + logs
        open(OUT, "w", encoding="utf-8").write(text)
        print(text[-2500:].encode("ascii", "replace").decode("ascii"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
