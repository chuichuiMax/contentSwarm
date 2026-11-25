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
        # recent failed runs
        rows = await conn.fetch(
            """
            select id, thread_id, status, error_message, input_payload
            from agent_runs
            where created_at > now() - interval '2 hours'
            order by created_at desc
            limit 20
            """
        )
        print("RUNS", len(rows))
        for row in rows:
            d = dict(row)
            err = d.get("error_message")
            status = d.get("status")
            print("RUN", json.dumps({
                "id": d.get("id"),
                "thread_id": d.get("thread_id"),
                "status": status,
                "error": err,
            }, ensure_ascii=False, default=str)[:1200])
            if err and ("NoneType" in str(err) or "strip" in str(err)):
                payload = d.get("input_payload")
                if isinstance(payload, str):
                    try: payload = json.loads(payload)
                    except Exception: payload = {"_raw": payload[:500]}
                if isinstance(payload, dict):
                    print("PAYLOAD_KEYS", list(payload.keys())[:40])
                    print("STYLE", json.dumps(payload.get("creative_style"), ensure_ascii=False, default=str)[:500])
                    print("VISUAL", json.dumps(payload.get("visual_material"), ensure_ascii=False, default=str)[:800])
                    print("BRIEF_KEYS", list((payload.get("content_brief") or {}).keys())[:30] if isinstance(payload.get("content_brief"), dict) else type(payload.get("content_brief")))
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
        with sftp.file("/tmp/_diag_none_strip2.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        db = run(
            c,
            "docker cp /tmp/_diag_none_strip2.py api-dev:/tmp/_diag_none_strip2.py && "
            "timeout 60 docker exec -w /app api-dev python /tmp/_diag_none_strip2.py",
            timeout=90,
        )
        logs = run(
            c,
            "docker logs api-dev --since 30m 2>&1 | python3 -c \""
            "import sys; t=sys.stdin.read(); "
            "idx=t.rfind('NoneType'); "
            "print(t[max(0,idx-2500):idx+1500] if idx>=0 else 'NO_NoneType_IN_30m'); "
            "idx2=t.rfind('AttributeError'); "
            "print('---'); print(t[max(0,idx2-1500):idx2+800] if idx2>=0 else 'NO_AttributeError')\"",
            timeout=90,
        )
        out = db + "\n====LOGS====\n" + logs
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii")[-14000:])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
