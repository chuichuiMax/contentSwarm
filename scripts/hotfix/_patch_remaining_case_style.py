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
import os, asyncio, asyncpg
OLD = "用口语化叙述描述图片中的设计细节（配色、布局、收纳等）。遇到专业术语（如工艺做法、材料名称、设计名词）直接用，不需要翻译成大白话。少修饰词，多说事实和逻辑。像现场给业主指着实物讲解。"
NEW = "用口语化叙述写整体亮点和住进去的感觉（配色、布局、收纳点到为止）。不要逐件细描构件、灯具、家具触感。专业术语可直接用，少修饰词，多说事实。"
async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            """
            select cs.id, cs.name, cs.prompt
            from creative_styles cs
            join content_types ct on ct.id = cs.content_type_id
            where ct.type_code = 'CT01' or ct.name like '%案例%'
            """
        )
        n = 0
        for r in rows:
            p = r["prompt"] or ""
            if OLD in p:
                await conn.execute("update creative_styles set prompt=$1 where id=$2", p.replace(OLD, NEW), r["id"])
                n += 1
                print("updated", r["name"])
        print("UPDATED", n)
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
        with sftp.file("/tmp/_patch_case_style.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_patch_case_style.py api-dev:/tmp/_patch_case_style.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_patch_case_style.py",
        )
        OUT.write_text(out, encoding="utf-8")
        print(out)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
