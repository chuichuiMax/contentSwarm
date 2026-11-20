#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK_ID = "ct_21e40f7dae3d41dabc674fb56aad25a8"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = f'''
import os, json, asyncio, asyncpg, re

TASK_ID = "{TASK_ID}"
CRAFT = re.compile(
    r"(龙骨|封板|嵌缝|腻子|螺钉|靠尺|塞尺|验收|工艺|找平|垂直度|平整度|"
    r"V字缝|纸带|钉帽|副龙骨|轻钢|基层|收口做法|施工节点)"
)

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            """
            SELECT t.id AS task_id, t.content_type_code, t.name,
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
        hits = []
        for i, p in enumerate(paras):
            found = sorted(set(CRAFT.findall(p)))
            if found:
                hits.append({{"index": i, "terms": found, "para": p}})
        print(json.dumps({{
            "task_id": None if not row else row["task_id"],
            "name": None if not row else row["name"],
            "content_type_code": None if not row else row["content_type_code"],
            "artifact_id": None if not row else row["artifact_id"],
            "title": None if not row else row["title"],
            "para_count": len(paras),
            "craft_hits": hits,
            "paras": paras,
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
        with sftp.file("/tmp/_inspect_21e40f.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_21e40f.py api-dev:/tmp/_inspect_21e40f.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_inspect_21e40f.py",
            timeout=55,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:2500])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
