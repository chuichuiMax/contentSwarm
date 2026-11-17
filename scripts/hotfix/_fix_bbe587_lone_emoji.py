#!/usr/bin/env python3
"""Deploy lone-emoji merge; patch ct_bbe587ee artifact."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().with_suffix(".out.txt")
ARTIFACT_ID = "ca_07059efa1e5c4201b9bb8cbde594e8d8"

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
]

PATCH = f'''
import os, json, asyncio, asyncpg, re
from yuxi.utils.line_breaks import normalize_published_body

ARTIFACT_ID = "{ARTIFACT_ID}"
LONE = re.compile(
    r"^[\\U0001F300-\\U0001FAFF\\U00002700-\\U000027BF\\U0001F600-\\U0001F64F\\U0001F680-\\U0001F6FF\\s]+$"
)

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        row = await conn.fetchrow(
            "select title, body from content_artifacts where id=$1", ARTIFACT_ID
        )
        if not row:
            print(json.dumps({{"error": "missing"}}))
            return
        old_body = row["body"] or ""
        new_body = normalize_published_body(old_body)
        await conn.execute(
            "update content_artifacts set body=$1, updated_at=now() where id=$2",
            new_body, ARTIFACT_ID,
        )
        paras = [p.strip() for p in re.split(r"\\n\\s*\\n", new_body) if p.strip()]
        lone = [i for i, p in enumerate(paras) if LONE.fullmatch(p)]
        print(json.dumps({{
            "title": row["title"],
            "changed": old_body != new_body,
            "blank_para": new_body.count("\\n\\n"),
            "lone_emoji_para_indexes": lone,
            "has_search_emoji": "\\U0001F50D" in new_body,
            "has_bulb_emoji": "\\U0001F4A1" in new_body,
            "body_sample": new_body[:160],
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
        with sftp.file("/tmp/_patch_bbe587.py", "w") as f:
            f.write(PATCH)
        sftp.close()
        print(
            run(
                c,
                "python3 -m py_compile "
                "/srv/contentswarm/backend/package/yuxi/utils/line_breaks.py "
                "/srv/contentswarm/backend/package/yuxi/content/generation.py "
                "&& echo SYNTAX_OK",
            )
        )
        patch_out = run(
            c,
            "docker cp /tmp/_patch_bbe587.py api-dev:/tmp/_patch_bbe587.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_patch_bbe587.py",
            timeout=55,
        )
        print("patched", "lone_emoji_para_indexes" in patch_out)
        for i in range(18):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' "
                "http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print("health", i, code)
            if code.endswith("200"):
                break
            time.sleep(2)
        pytest_out = run(
            c,
            "timeout 90 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_normalize_published_body.py -q --tb=line",
            timeout=110,
        )
        OUT.write_text(patch_out + "\n===pytest===\n" + pytest_out, encoding="utf-8")
        print(patch_out.encode("ascii", "replace").decode("ascii"))
        print(pytest_out[-600:].encode("ascii", "replace").decode("ascii"))
        ok_patch = '"lone_emoji_para_indexes": []' in patch_out and '"changed": true' in patch_out
        ok_test = "passed" in pytest_out and "failed" not in pytest_out.split("passed")[-1][:40]
        return 0 if ok_patch and ok_test else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
