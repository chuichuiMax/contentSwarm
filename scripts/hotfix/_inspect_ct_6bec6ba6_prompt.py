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
        art = await conn.fetchrow(
            "select runtime_config_snapshot, evidence_usage_snapshot, edit_diff_snapshot "
            "from content_artifacts where task_id=$1 order by id desc limit 1",
            TASK,
        )
        if art:
            for key in ("runtime_config_snapshot", "evidence_usage_snapshot", "edit_diff_snapshot"):
                val = art[key]
                if isinstance(val, str):
                    try: val = json.loads(val)
                    except Exception: pass
                if isinstance(val, dict):
                    # print useful keys
                    print("KEY", key, list(val.keys())[:40])
                    for sub in ("generation_prompt", "user_request", "prompt", "direct_prompt", "model_prompt"):
                        if sub in val:
                            print(sub.upper(), str(val[sub])[:1500])
                    raw = val.get("raw_business_json") if isinstance(val.get("raw_business_json"), dict) else None
                    if raw:
                        print("RAW_KEYS", list(raw.keys()))
                else:
                    print("KEY", key, type(val).__name__)
        # also check run call logs if any
        tables = await conn.fetch(
            "select table_name from information_schema.tables where table_schema='public' "
            "and (table_name ilike '%call%log%' or table_name ilike '%generation%' or table_name ilike '%run%')"
        )
        print("CANDIDATE_TABLES", [r["table_name"] for r in tables])
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
        with sftp.file("/tmp/_inspect_ct_prompt.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_ct_prompt.py api-dev:/tmp/_inspect_ct_prompt.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_inspect_ct_prompt.py",
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:5000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
