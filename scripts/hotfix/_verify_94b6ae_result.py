#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PY = r'''
import os, json, asyncio, asyncpg
async def main():
    url=(os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://","postgresql://")
    conn=await asyncpg.connect(url)
    row=await conn.fetchrow(
        "select title, body, topics::text as topics from content_artifacts where id=$1",
        "ca_38fcf4abad264d9b8a737931ef52afbe",
    )
    topics=json.loads(row["topics"])
    body=row["body"] or ""
    print(json.dumps({
        "title": row["title"],
        "topics": topics,
        "has_detail_tags": any(x in topics for x in ["沙发背景墙木饰面","木饰面房门","客厅无主灯射灯"]),
        "has_cta": "先收藏这份验收要点" in body,
        "body": body,
    }, ensure_ascii=False, indent=2))
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


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_verify_94b6.py", "w") as f:
            f.write(PY)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_verify_94b6.py api-dev:/tmp/_verify_94b6.py && "
            "timeout 30 docker exec -w /app api-dev python /tmp/_verify_94b6.py",
        )
        OUT.write_text(out, encoding="utf-8")
        print(f"wrote {OUT}")
        start = out.find("{")
        if start >= 0:
            import json
            data = json.loads(out[start:])
            print("topics:", data["topics"])
            print("has_detail_tags:", data["has_detail_tags"])
            print("has_cta:", data["has_cta"])
        # run focused unit tests in container
        print("=== unit tests ===")
        # sync test file too
        local_test = Path(__file__).resolve().parents[2] / "backend/test/unit/content/test_direct_content_generation.py"
        sftp = c.open_sftp()
        sftp.put(str(local_test), "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py")
        sftp.close()
        print(
            run(
                c,
                "timeout 120 docker exec -w /app api-dev "
                "uv run --no-sync pytest test/unit/content/test_direct_content_generation.py -q --tb=line",
                timeout=130,
            )[-2000:]
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
