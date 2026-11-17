#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_6959f64f830b4c5687686268aa16d03d"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = f'''
import os, json, asyncio, asyncpg, re

TASK_ID = "{TASK_ID}"
EMOJI = re.compile(r"[\\U0001F300-\\U0001FAFF\\U00002700-\\U000027BF\\U0001F600-\\U0001F64F\\U0001F680-\\U0001F6FF]")

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            """
            SELECT t.status, t.content_type_code,
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
        runtime = json.loads(row["runtime"] or "{{}}")
        body = row["body"] or ""
        title = row["title"] or ""
        out = {{
            "status": row["status"],
            "type": row["content_type_code"],
            "artifact": row["artifact_id"],
            "title": title,
            "topics": json.loads(row["topics"] or "[]"),
            "body": body,
            "body_repr": repr(body),
            "newline_count": body.count("\\n"),
            "blank_para": body.count("\\n\\n"),
            "has_space_sentence_join": "。 " in body or "! " in body or "？ " in body,
            "title_emoji": bool(EMOJI.search(title)),
            "body_emoji": bool(EMOJI.search(body)),
            "emoji_in_title": EMOJI.findall(title),
            "emoji_in_body": EMOJI.findall(body)[:20],
            "generation_prompt_tail": (runtime.get("generation_prompt") or "")[-800:],
            "prompt_log_head": ((runtime.get("generation_log") or {{}}).get("prompt") or "")[:1200],
            "creative_style": runtime.get("creative_style") or {{}},
            "image_attached": ((runtime.get("generation_log") or {{}}).get("image_attached")),
        }}
        print(json.dumps(out, ensure_ascii=False, indent=2))
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
        with sftp.file("/tmp/_inspect_6959.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_6959.py api-dev:/tmp/_inspect_6959.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_6959.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(f"wrote {OUT}")
        start = out.find("{")
        if start >= 0:
            data = json.loads(out[start:])
            print("type", data.get("type"), "artifact", data.get("artifact"))
            print("blank_para", data.get("blank_para"), "space_join", data.get("has_space_sentence_join"))
            print("title_emoji", data.get("title_emoji"), "body_emoji", data.get("body_emoji"))
            print("title_ascii", data.get("title", "").encode("ascii", "replace").decode())
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
