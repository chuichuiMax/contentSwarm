#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"

MINT = r'''
import asyncio
from datetime import timedelta
from sqlalchemy import select
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

create_access_token = None
for mod in ("server.utils.auth_utils", "server.utils.user_utils", "yuxi.utils.auth"):
    try:
        m = __import__(mod, fromlist=["create_access_token"])
        create_access_token = getattr(m, "create_access_token")
        print("token_mod", mod)
        break
    except Exception as e:
        print("skip", mod, type(e).__name__)

if create_access_token is None:
    raise SystemExit("no create_access_token")

async def main():
    await pg_manager.initialize()
    async with pg_manager.get_session() as db:
        u = (await db.execute(select(User).limit(1))).scalar_one()
        token = create_access_token({"sub": str(u.uid)}, expires_delta=timedelta(minutes=30))
        print("UID", u.uid)
        print("TOKEN", token)

asyncio.run(main())
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
        print("=== patch snippet ===")
        print(run(c, "sed -n '2640,2675p' /srv/contentswarm/backend/package/yuxi/services/material_library_service.py"))

        sftp = c.open_sftp()
        with sftp.file("/tmp/_mint_token.py", "w") as f:
            f.write(MINT)
        sftp.close()
        print("=== mint ===")
        out = run(
            c,
            "docker cp /tmp/_mint_token.py api-dev:/tmp/_mint_token.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_mint_token.py",
            timeout=75,
        )
        print(out)
        token = None
        for line in out.splitlines():
            if line.startswith("TOKEN "):
                token = line[6:].strip()
        if not token:
            print("NO_TOKEN")
            return 1

        # Call from remote host with token
        print("=== call galleries ===")
        print(
            run(
                c,
                "python3 - <<'PY'\n"
                "import urllib.request, urllib.error\n"
                f"token={token!r}\n"
                "url='http://127.0.0.1:5050/api/material-library/galleries?industry_slug=decoration'\n"
                "req=urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})\n"
                "try:\n"
                "  with urllib.request.urlopen(req, timeout=30) as resp:\n"
                "    body=resp.read()\n"
                "    print('status', resp.status)\n"
                "    print(body[:500].decode('utf-8','replace'))\n"
                "except urllib.error.HTTPError as e:\n"
                "  print('status', e.code)\n"
                "  print(e.read()[:800].decode('utf-8','replace'))\n"
                "PY",
                timeout=45,
            )
        )
        print("=== recent errors ===")
        print(
            run(
                c,
                "docker logs api-dev --since 2m 2>&1 | grep -E 'galleries|AttributeError|NameError| 500 ' | tail -20",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
