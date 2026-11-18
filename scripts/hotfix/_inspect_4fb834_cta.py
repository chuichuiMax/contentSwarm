#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_4fb83476f1114129896ca8197030bc99"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = f'''
import os, json, asyncio, asyncpg, re

TASK_ID = "{TASK_ID}"
CTA = re.compile(r"(收藏|评论区|留言|关注我|对照一遍|照着对|进场对照|想弄明白|想看工地|想弄清)")

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            """
            SELECT t.id AS task_id, t.content_type_code,
                   a.id AS artifact_id, a.title, a.body
            FROM content_tasks t
            LEFT JOIN content_artifacts a ON a.task_id = t.id
            WHERE t.id = $1
            ORDER BY a.created_at DESC NULLS LAST
            LIMIT 1
            """,
            TASK_ID,
        )
        body = (row["body"] or "") if row else ""
        paras = [p for p in re.split(r"\\n\\s*\\n", body) if p.strip()]
        cta_idxs = [i for i,p in enumerate(paras) if CTA.search(p)]
        print(json.dumps({{
            "task_id": None if not row else row["task_id"],
            "content_type_code": None if not row else row["content_type_code"],
            "artifact_id": None if not row else row["artifact_id"],
            "title": None if not row else row["title"],
            "para_count": len(paras),
            "cta_indexes": cta_idxs,
            "tail": paras[-4:],
            "body": body,
        }}, ensure_ascii=False, indent=2))
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
        raise TimeoutError(cmd[:100])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_inspect_4fb834.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_4fb834.py api-dev:/tmp/_inspect_4fb834.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_4fb834.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:3000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
