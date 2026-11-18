#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_0d53388580aa4424a5be92d5e398eb61"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = f'''
import os, json, asyncio, asyncpg

TASK_ID = "{TASK_ID}"

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            """
            SELECT t.id AS task_id, t.content_type_code, a.id AS artifact_id, a.title, a.body
            FROM content_tasks t
            LEFT JOIN content_artifacts a ON a.task_id = t.id
            WHERE t.id = $1
            ORDER BY a.created_at DESC NULLS LAST
            LIMIT 1
            """,
            TASK_ID,
        )
        body = (row["body"] or "") if row else ""
        paras = [p for p in body.split("\\n\\n") if p.strip()]
        print(json.dumps({{
            "row": None if not row else {{
                "task_id": row["task_id"],
                "content_type_code": row["content_type_code"],
                "artifact_id": row["artifact_id"],
                "title": row["title"],
            }},
            "para_count": len(paras),
            "paras": paras,
            "tail": paras[-3:] if paras else [],
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
        with sftp.file("/tmp/_inspect_0d5338.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_0d5338.py api-dev:/tmp/_inspect_0d5338.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_0d5338.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:3000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
