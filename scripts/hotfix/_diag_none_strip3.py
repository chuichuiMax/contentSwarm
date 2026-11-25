#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


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


REMOTE = r'''
import asyncio, json, os, asyncpg

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        tables = await conn.fetch(
            "select table_name from information_schema.tables where table_schema='public' "
            "and (table_name ilike '%run%' or table_name ilike '%agent%' or table_name ilike '%content%')"
        )
        print("TABLES", [r["table_name"] for r in tables])
        for table in [r["table_name"] for r in tables]:
            if "run" not in table:
                continue
            cols = [c["column_name"] for c in await conn.fetch(
                "select column_name from information_schema.columns where table_name=$1", table
            )]
            print("COLS", table, cols[:40])
            # try fetch recent
            order = "created_at" if "created_at" in cols else cols[0]
            rows = await conn.fetch(f"select * from {table} order by {order} desc limit 5")
            for row in rows:
                d = dict(row)
                slim = {k: d.get(k) for k in d if k in (
                    "id","status","error","error_message","message","thread_id","task_id","run_id","updated_at","created_at"
                ) or "error" in k or "status" in k}
                print("ROW", table, json.dumps(slim, ensure_ascii=False, default=str)[:1500])

        # specific task from logs
        task = await conn.fetchrow("select * from content_tasks where id=$1", "ct_f19c6636bc24454a8464a08d341fd5e9")
        if task:
            print("TASK", json.dumps({k: dict(task).get(k) for k in ("id","status","name","latest_run_id","error_message")}, ensure_ascii=False, default=str))
            print("TASK_KEYS", list(dict(task).keys())[:50])
    finally:
        await conn.close()

asyncio.run(main())
'''


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_diag_none_strip3.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        db = run(
            c,
            "docker cp /tmp/_diag_none_strip3.py api-dev:/tmp/_diag_none_strip3.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_diag_none_strip3.py",
            timeout=90,
        )
        # search all containers for the error
        logs = run(
            c,
            "docker ps --format '{{.Names}}' | while read n; do "
            "echo ==== $n ====; docker logs \"$n\" --since 2h 2>&1 | "
            "python3 -c 'import sys; t=sys.stdin.read();\n"
            "idx=t.rfind(\"NoneType\");\n"
            "print(t[max(0,idx-2000):idx+1200] if idx>=0 else \"none\")' ; done",
            timeout=120,
        )
        out = db + "\n====LOGS====\n" + logs
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii")[-16000:])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
