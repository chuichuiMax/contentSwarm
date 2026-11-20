#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE = r'''
import asyncio, json, os, asyncpg

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            """
            select a.id, a.task_id, a.title, a.topics, a.cover_job_id, a.cover_asset_id,
                   a.hycanvas_design_snapshot, a.runtime_config_snapshot
            from content_artifacts a
            where a.title like $1
            order by a.id desc
            limit 5
            """,
            "%坐在这样的客厅里%",
        )
        print("COUNT", len(rows))
        for row in rows:
            d = dict(row)
            print("ART", json.dumps({
                "id": d.get("id"),
                "task_id": d.get("task_id"),
                "title": d.get("title"),
                "topics": d.get("topics"),
                "cover_job_id": d.get("cover_job_id"),
                "cover_asset_id": d.get("cover_asset_id"),
            }, ensure_ascii=False, default=str))
            snap = d.get("hycanvas_design_snapshot")
            if isinstance(snap, str):
                try: snap = json.loads(snap)
                except Exception: snap = {}
            if isinstance(snap, dict):
                print("HY", json.dumps({k: snap.get(k) for k in ("design_id","template_id","fields","source_image_asset_id")}, ensure_ascii=False, default=str))
            runtime = d.get("runtime_config_snapshot")
            if isinstance(runtime, str):
                try: runtime = json.loads(runtime)
                except Exception: runtime = {}
            if isinstance(runtime, dict):
                print("RT_KEYS", list(runtime.keys())[:30])
                for key in ("visual_material", "generation_log", "creative_style"):
                    if key in runtime:
                        print("RT", key, json.dumps(runtime[key], ensure_ascii=False, default=str)[:1500])
            job_id = d.get("cover_job_id")
            if job_id:
                job = await conn.fetchrow("select id, mode, status, request_json, result_json from content_cover_jobs where id=$1", job_id)
                if job:
                    jd = dict(job)
                    print("JOB", json.dumps({
                        "id": jd.get("id"), "mode": jd.get("mode"), "status": jd.get("status"),
                        "request": jd.get("request_json"),
                    }, ensure_ascii=False, default=str)[:2500])
            task = await conn.fetchrow(
                "select id, selected_image_item_id, content_type_code from content_tasks where id=$1",
                d.get("task_id"),
            )
            if task:
                print("TASK", json.dumps(dict(task), ensure_ascii=False, default=str))
    finally:
        await conn.close()

asyncio.run(main())
'''


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


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_inspect_title_tags_case.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_title_tags_case.py api-dev:/tmp/_inspect_title_tags_case.py && "
            "timeout 90 docker exec -w /app api-dev python /tmp/_inspect_title_tags_case.py",
            timeout=120,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "backslashreplace").decode("ascii")[-12000:])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
