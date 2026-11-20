#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
TASK = "ct_e4788cac9ba5461f8ccbc284cdd0d802"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE = r'''
import os, asyncio, asyncpg, json
TASK = "ct_e4788cac9ba5461f8ccbc284cdd0d802"

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        task = await conn.fetchrow(
            "select id, name, status, content_type_code, selected_image_item_id, selected_poster_template_id, "
            "cover_asset_id if false else null as dummy from content_tasks where id=$1",
            TASK,
        )
    except Exception:
        task = await conn.fetchrow("select * from content_tasks where id=$1", TASK)
    d = dict(task) if task else {}
    print("TASK", json.dumps({k: d.get(k) for k in (
        "id","name","status","content_type_code","selected_image_item_id","selected_poster_template_id","latest_run_id"
    ) if k in d}, ensure_ascii=False, default=str))

    art = await conn.fetchrow(
        "select id, title, cover_asset_id, cover_job_id, hycanvas_design_snapshot, content_type_snapshot, "
        "runtime_config_snapshot from content_artifacts where task_id=$1 order by id desc limit 1",
        TASK,
    )
    if art:
        ad = dict(art)
        print("ART", json.dumps({
            "id": ad.get("id"),
            "title": ad.get("title"),
            "cover_asset_id": ad.get("cover_asset_id"),
            "cover_job_id": ad.get("cover_job_id"),
        }, ensure_ascii=False, default=str))
        snap = ad.get("hycanvas_design_snapshot")
        if isinstance(snap, str):
            try: snap = json.loads(snap)
            except Exception: snap = {"_raw": snap[:500]}
        if isinstance(snap, dict):
            print("HYCANVAS_KEYS", list(snap.keys())[:40])
            for key in ("templateId","template_id","designId","design_id","width","height","canvas","export","previewUrl","imageUrl"):
                if key in snap:
                    print("HY", key, snap.get(key))
        runtime = ad.get("runtime_config_snapshot")
        if isinstance(runtime, str):
            try: runtime = json.loads(runtime)
            except Exception: runtime = {}
        if isinstance(runtime, dict):
            print("RUNTIME_KEYS", list(runtime.keys())[:40])
            for key in ("cover","visual_material","selected_cover","poster","hycanvas","ai_cover","generation_log"):
                if key in runtime:
                    val = runtime[key]
                    print("RT", key, json.dumps(val, ensure_ascii=False, default=str)[:1200])

    # cover-related tables
    tables = await conn.fetch(
        "select table_name from information_schema.tables where table_schema='public' "
        "and (table_name ilike '%cover%' or table_name ilike '%asset%' or table_name ilike '%hycanvas%' "
        "or table_name ilike '%poster%')"
    )
    print("TABLES", [r["table_name"] for r in tables])

    for table in [r["table_name"] for r in tables]:
        cols = [c["column_name"] for c in await conn.fetch(
            "select column_name from information_schema.columns where table_name=$1", table
        )]
        if "task_id" in cols:
            row = await conn.fetchrow(f"select * from {table} where task_id=$1 order by 1 desc limit 1", TASK)
            if row:
                rd = dict(row)
                print("ROW", table, json.dumps({k: rd.get(k) for k in list(rd)[:30]}, ensure_ascii=False, default=str)[:2000])
        if "artifact_id" in cols and art:
            row = await conn.fetchrow(
                f"select * from {table} where artifact_id=$1 order by 1 desc limit 1", art["id"]
            )
            if row:
                rd = dict(row)
                print("ROW", table, json.dumps({k: rd.get(k) for k in list(rd)[:30]}, ensure_ascii=False, default=str)[:2000])

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
        with sftp.file("/tmp/_inspect_ct_cover.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_inspect_ct_cover.py api-dev:/tmp/_inspect_ct_cover.py && "
            "timeout 70 docker exec -w /app api-dev python /tmp/_inspect_ct_cover.py",
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[:9000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
