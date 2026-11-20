#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE = r'''
import os, asyncio, asyncpg, re
MARKERS = ["具体设计元素", "逐一介绍", "设计元素展开", "所有设计描述、颜色、材质", "设计细节（配色"]
async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            """
            select cs.name, cs.prompt, ct.name as type_name
            from creative_styles cs
            join content_types ct on ct.id = cs.content_type_id
            where ct.type_code = 'CT01' or ct.name like '%案例%'
            """
        )
        for r in rows:
            hits = [m for m in MARKERS if m in (r["prompt"] or "")]
            print("STYLE", r["name"], "hits", hits)
            if hits:
                # print nearby snippets
                p = r["prompt"] or ""
                for m in hits:
                    i = p.find(m)
                    print("  snip", p[max(0,i-20):i+60].replace("\n"," "))
    finally:
        await conn.close()
asyncio.run(main())
'''


def run(c, cmd, timeout=60):
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
        raise TimeoutError(cmd[:80])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_check_case_styles.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_check_case_styles.py api-dev:/tmp/_check_case_styles.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_check_case_styles.py",
        )
        OUT.write_text(out, encoding="utf-8")
        print(out)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
