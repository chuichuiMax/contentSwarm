#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK = "ct_6bec6ba63c214fa5815bba2af6fb6452"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE = r'''
import os, asyncio, asyncpg, json
TASK = "ct_6bec6ba63c214fa5815bba2af6fb6452"

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            "select id, name, status, content_type_code, content_goal, selected_image_item_id, "
            "brief_json, runtime_config_snapshot_json from content_tasks where id=$1",
            TASK,
        )
        d = dict(row) if row else {}
        brief = d.get("brief_json")
        if isinstance(brief, str):
            try: brief = json.loads(brief)
            except Exception: brief = {}
        runtime = d.get("runtime_config_snapshot_json")
        if isinstance(runtime, str):
            try: runtime = json.loads(runtime)
            except Exception: runtime = {}
        raw = (runtime or {}).get("raw_business_json") if isinstance(runtime, dict) else {}
        print("META", json.dumps({
            "id": d.get("id"),
            "name": d.get("name"),
            "status": d.get("status"),
            "content_type_code": d.get("content_type_code"),
            "content_goal": d.get("content_goal"),
            "selected_image_item_id": d.get("selected_image_item_id"),
            "contentType": (raw or {}).get("contentType") if isinstance(raw, dict) else None,
            "businessVariables": (raw or {}).get("businessVariables") if isinstance(raw, dict) else None,
        }, ensure_ascii=False, default=str))
        # find artifact table/columns
        arts = await conn.fetch(
            "select table_name from information_schema.tables "
            "where table_schema='public' and table_name ilike '%artifact%'"
        )
        print("ART_TABLES", [r["table_name"] for r in arts])
        for table in [r["table_name"] for r in arts]:
            cols = await conn.fetch(
                "select column_name from information_schema.columns where table_name=$1",
                table,
            )
            colnames = [c["column_name"] for c in cols]
            print("COLS", table, colnames)
            if "task_id" in colnames and ("body" in colnames or "content" in colnames):
                body_col = "body" if "body" in colnames else "content"
                title_col = "title" if "title" in colnames else None
                select_cols = ["id", body_col] + ([title_col] if title_col else [])
                q = f"select {', '.join(select_cols)} from {table} where task_id=$1 order by id desc limit 1"
                art = await conn.fetchrow(q, TASK)
                if art:
                    ad = dict(art)
                    print("ART_ID", ad.get("id"))
                    if title_col:
                        print("TITLE", ad.get(title_col))
                    print("BODY_BEGIN")
                    print(ad.get(body_col) or "")
                    print("BODY_END")
                    break
    finally:
        await conn.close()

asyncio.run(main())
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
        with sftp.file("/tmp/_inspect_ct_6bec6ba6.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_ct_6bec6ba6.py api-dev:/tmp/_inspect_ct_6bec6ba6.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_inspect_ct_6bec6ba6.py",
            timeout=90,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:9000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
