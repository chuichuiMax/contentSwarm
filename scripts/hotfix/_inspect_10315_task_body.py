"""Inspect content task body/title on 172.16.103.15 for format + typo."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
TASK_ID = "ct_9ec75dff1cb44dad8fa905feff5a7fa9"


def _pw() -> str:
    for key in ("DEPLOY_SSH_PASSWORD", "CONTENTSWARM_TEST_SSH_PASSWORD"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    py = r'''
import json, asyncio
from sqlalchemy import text
from yuxi.storage.postgres.manager import pg_manager

TASK_ID = "ct_9ec75dff1cb44dad8fa905feff5a7fa9"

async def main():
    async with pg_manager.get_async_session_context() as db:
        cols = (await db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='content_artifacts' ORDER BY ordinal_position
        """))).scalars().all()
        print("artifact_columns:", list(cols))
        rows = (await db.execute(text("""
            SELECT t.id, t.status, t.content_type_code,
                   a.id AS artifact_id, a.created_at,
                   a.title, a.body, a.topics
            FROM content_tasks t
            LEFT JOIN content_artifacts a ON a.task_id = t.id
            WHERE t.id = :tid
            ORDER BY a.created_at DESC NULLS LAST
            LIMIT 5
        """), {"tid": TASK_ID})).mappings().all()
        print("ROWS", len(rows))
        for row in rows:
            body = row.get("body") or ""
            print("---")
            print("artifact", row.get("artifact_id"), "created", row.get("created_at"), "status", row.get("status"), "type", row.get("content_type_code"))
            print("title:", (row.get("title") or "")[:200])
            print("body_len:", len(body))
            print("newline_count:", body.count(chr(10)), "blank_para:", body.count(chr(10)+chr(10)))
            print("has_zaijian:", "在建工地" in body, "has_zaishou:", "在手工地" in body)
            print("body_repr_head:", repr(body[:800]))
            # versions table
        vcols = (await db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='content_versions' ORDER BY ordinal_position
        """))).scalars().all()
        print("version_columns:", list(vcols))
        if vcols:
            vers = (await db.execute(text("""
                SELECT id, created_at, title, left(coalesce(body,''), 200) AS body_head,
                       position('在手工地' in coalesce(body,'')) AS zaishou_pos,
                       position('在建工地' in coalesce(body,'')) AS zaijian_pos
                FROM content_versions WHERE task_id = :tid
                ORDER BY created_at DESC LIMIT 5
            """), {"tid": TASK_ID})).mappings().all()
            print("VERSIONS", len(vers))
            for v in vers:
                print(dict(v))

asyncio.run(main())
'''
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    # write temp script into container via stdin
    cmd = "docker exec -i api-dev uv run --no-sync python -"
    stdin, stdout, stderr = c.exec_command(cmd, timeout=120)
    stdin.write(py)
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    report = Path(__file__).resolve().parents[2] / "scripts" / "hotfix" / "_inspect_10315_task_body.out.txt"
    report.write_text(out + ("\n---stderr---\n" + err if err.strip() else ""), encoding="utf-8")
    print(f"wrote {report}")
    print("zaishou" in out, "zaijian" in out, "newline_count" in out)
    for line in out.splitlines():
        if line.startswith(("ROWS", "artifact", "title:", "body_len", "newline", "has_", "VERSION", "artifact_columns", "version_columns", "---")):
            sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
