#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_220354dd141144589e89bc7f0d1d353c"
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
                   t.selected_title_json::text AS selected_title,
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
        brief = json.loads(row["brief"] or "{{}}")
        runtime = json.loads(row["runtime"] or "{{}}")
        topics = json.loads(row["topics"] or "[]")
        selected = json.loads(row["selected_title"] or "null")
        # dig craft theme from nested brief
        craft = None
        for container in (brief, brief.get("facts") or {{}}, brief.get("business_variables") or {{}}, brief.get("businessVariables") or {{}}, brief.get("form_values") or {{}}):
            if isinstance(container, dict):
                for key in ("工艺主题", "craft_theme", "工艺", "theme"):
                    if container.get(key):
                        craft = container.get(key)
                        break
        out = {{
            "status": row["status"],
            "type": row["content_type_code"],
            "artifact": row["artifact_id"],
            "title": row["title"],
            "selected_title": selected,
            "topics": topics,
            "body": row["body"],
            "craft_theme": craft,
            "generation_prompt": (runtime.get("generation_prompt") or "")[:1500],
            "creative_style": runtime.get("creative_style") or {{}},
            "knowledge_hits": ((runtime.get("generation_log") or {{}}).get("knowledge_hits") or []),
            "image_attached": ((runtime.get("generation_log") or {{}}).get("image_attached")),
            "prompt_log_head": ((runtime.get("generation_log") or {{}}).get("prompt") or "")[:2000],
            "brief_dump": json.dumps(brief, ensure_ascii=False)[:2500],
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
        with sftp.file("/tmp/_inspect_220.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_220.py api-dev:/tmp/_inspect_220.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_220.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(f"wrote {OUT}")
        start = out.find("{")
        if start >= 0:
            data = json.loads(out[start:])
            print("title:", data.get("title"))
            print("type:", data.get("type"), "image:", data.get("image_attached"))
            print("craft:", data.get("craft_theme"))
            print("topics:", data.get("topics"))
            print("body_tail:", repr((data.get("body") or "")[-180:]))
        else:
            print(out[-500:])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
