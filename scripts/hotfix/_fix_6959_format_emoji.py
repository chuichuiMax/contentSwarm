#!/usr/bin/env python3
"""Deploy blank-paragraph + emoji rules; patch ct_6959... artifact."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().with_suffix(".out.txt")
ARTIFACT_ID = "ca_54f45459bde14d5ca63fde17f5342d80"

FILES = [
    (
        ROOT / "backend/package/yuxi/utils/line_breaks.py",
        "/srv/contentswarm/backend/package/yuxi/utils/line_breaks.py",
    ),
    (
        ROOT / "backend/package/yuxi/content/generation.py",
        "/srv/contentswarm/backend/package/yuxi/content/generation.py",
    ),
    (
        ROOT / "backend/test/unit/content/test_normalize_published_body.py",
        "/srv/contentswarm/backend/test/unit/content/test_normalize_published_body.py",
    ),
    (
        ROOT / "backend/test/unit/content/test_direct_content_generation.py",
        "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py",
    ),
]

NEW_TITLE = "床头墙面做拼接，缝隙没对齐真扎眼😭"
NEW_BODY = """床头那面墙，拼接位置没排好，一进卧室就想盯着看👀

几种板材交接，缝走不直，肉眼一眼就能挑出来。

所以这种墙面，先在图上把分缝位置排清楚。

竖向板条之间留多宽、怎么对齐，都要定下来。

板材进场先对色对纹，避免同一面墙深浅不一。

安装时用靠尺贴着量，缝宽上下保持一致📏

批腻子前，把所有接缝再检查一遍，不齐的先调整。

这道工序藏在漆面底下，做没做好，住久了才知道。

收藏这条，等家里木作进场那天，照着一条条对👍"""

PATCH = f'''
import os, json, asyncio, asyncpg
from yuxi.utils.line_breaks import normalize_published_body

ARTIFACT_ID = "{ARTIFACT_ID}"
NEW_TITLE = {NEW_TITLE!r}
NEW_BODY = normalize_published_body({NEW_BODY!r})

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "update content_artifacts set title=$1, body=$2, updated_at=now() where id=$3",
            NEW_TITLE, NEW_BODY, ARTIFACT_ID,
        )
        await conn.execute(
            "update content_tasks set selected_title_json=$1::jsonb, updated_at=now() "
            "where id=(select task_id from content_artifacts where id=$2)",
            json.dumps({{"text": NEW_TITLE}}, ensure_ascii=False),
            ARTIFACT_ID,
        )
        row = await conn.fetchrow(
            "select title, body from content_artifacts where id=$1", ARTIFACT_ID
        )
        body = row["body"] or ""
        print(json.dumps({{
            "title": row["title"],
            "blank_para": body.count("\\n\\n"),
            "has_emoji_title": any(ord(ch) > 0x1F300 for ch in (row["title"] or "")),
            "has_emoji_body": "👀" in body or "👍" in body or "📏" in body,
            "body_head": body[:80],
        }}, ensure_ascii=False))
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
        for local, remote in FILES:
            sftp.put(str(local), remote)
            print("uploaded", local.name)
        with sftp.file("/tmp/_patch_6959.py", "w") as f:
            f.write(PATCH)
        sftp.close()
        print(run(c, "python3 -m py_compile /srv/contentswarm/backend/package/yuxi/utils/line_breaks.py /srv/contentswarm/backend/package/yuxi/content/generation.py && echo SYNTAX_OK"))
        patch_out = run(
            c,
            "docker cp /tmp/_patch_6959.py api-dev:/tmp/_patch_6959.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_patch_6959.py",
            timeout=55,
        )
        print("patched", "blank_para" in patch_out)
        for i in range(18):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print("health", i, code)
            if code.endswith("200"):
                break
            time.sleep(2)
        pytest_out = run(
            c,
            "timeout 150 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_normalize_published_body.py "
            "test/unit/content/test_direct_content_generation.py "
            "-q --tb=line",
            timeout=170,
        )
        OUT.write_text(patch_out + "\n===pytest===\n" + pytest_out, encoding="utf-8")
        print(pytest_out[-900:].encode("ascii", "replace").decode("ascii"))
        return 0 if "passed" in pytest_out and "failed" not in pytest_out.split("passed")[-1][:40] else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
