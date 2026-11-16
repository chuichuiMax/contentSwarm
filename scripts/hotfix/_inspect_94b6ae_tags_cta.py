#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_94b6ae987973437fb4fa3f36ebc32c1d"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = f'''
import os, json, asyncio, asyncpg

TASK_ID = "{TASK_ID}"

async def main():
    url = (os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL") or "").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            """
            SELECT t.status, t.content_type_code, t.brief_json::text AS brief,
                   a.id AS artifact_id, a.title, a.body, a.topics::text AS topics,
                   a.runtime_config_snapshot::text AS runtime
            FROM content_tasks t
            LEFT JOIN content_artifacts a ON a.task_id = t.id
            WHERE t.id = $1
            ORDER BY a.created_at DESC NULLS LAST
            LIMIT 1
            """,
            TASK_ID,
        )
        data = dict(row)
        brief = json.loads(data["brief"] or "{{}}")
        runtime = json.loads(data["runtime"] or "{{}}")
        topics = json.loads(data["topics"] or "[]")
        out = {{
            "status": data["status"],
            "type": data["content_type_code"],
            "artifact": data["artifact_id"],
            "title": data["title"],
            "topics": topics,
            "body": data["body"],
            "audience_sample": (brief.get("audience") or [])[:30],
            "audience_count": len(brief.get("audience") or []),
            "generation_prompt": (runtime.get("generation_prompt") or "")[:500],
            "knowledge_hits": ((runtime.get("generation_log") or {{}}).get("knowledge_hits") or []),
            "image_attached": ((runtime.get("generation_log") or {{}}).get("image_attached")),
        }}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        await conn.close()

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
        raise TimeoutError(cmd[:100])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_inspect_94b6.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_94b6.py api-dev:/tmp/_inspect_94b6.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_94b6.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(f"wrote {OUT}")
        # print key lines only via utf-8 file we just wrote
        data = None
        try:
            # strip docker log noise before JSON
            start = out.find("{")
            if start >= 0:
                data = json.loads(out[start:])
        except Exception as e:
            print("parse_fail", e)
        if data:
            print("title:", data.get("title"))
            print("topics:", data.get("topics"))
            print("audience_sample:", data.get("audience_sample"))
            print("body_tail:", repr((data.get("body") or "")[-120:]))
            print("image_attached:", data.get("image_attached"))
            print("knowledge_hits:", data.get("knowledge_hits"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    import json

    raise SystemExit(main())
