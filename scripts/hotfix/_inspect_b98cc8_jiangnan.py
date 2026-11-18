#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_b98cc863f94345c7a194d89180b87345"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = f'''
import os, json, asyncio, asyncpg, re

TASK_ID = "{TASK_ID}"

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'content_tasks' ORDER BY ordinal_position
            """
        )
        col_names = [r["column_name"] for r in cols]
        print("task_columns", col_names)
        row = await conn.fetchrow(
            """
            SELECT t.*, a.id AS artifact_id, a.title AS artifact_title, a.body
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
        hits = [p for p in paras if "江南" in p or "留言" in p]
        data = dict(row) if row else {{}}
        # shrink large fields for readability
        for k, v in list(data.items()):
            if isinstance(v, str) and len(v) > 800 and k != "body":
                data[k] = v[:800] + "..."
        print(json.dumps({{
            "task": {{k: data.get(k) for k in data if k not in {{"body"}}}},
            "para_count": len(paras),
            "hits": hits,
            "tail": paras[-4:],
            "body": body,
        }}, ensure_ascii=False, indent=2, default=str))
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
        with sftp.file("/tmp/_inspect_b98cc8.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_b98cc8.py api-dev:/tmp/_inspect_b98cc8.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_b98cc8.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:3500])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
