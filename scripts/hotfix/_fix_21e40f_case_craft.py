#!/usr/bin/env python3
"""Deploy case-share no-craft rules; patch ct_21e40f artifact."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().with_suffix(".out.txt")
ARTIFACT_ID = "ca_5aa777550ade4c3a91c964a855952863"

FILES = [
    (
        ROOT / "backend/package/yuxi/content/generation.py",
        "/srv/contentswarm/backend/package/yuxi/content/generation.py",
    ),
    (
        ROOT / "backend/test/unit/content/test_direct_content_generation.py",
        "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py",
    ),
]

NEW_BODY = """一进门脚步声都轻了半截，耳朵先松了一口气🍃

墙面是灰调的艺术肌理，摸上去有细细的起伏，不平滑，反而显得沉。

吊顶做成整片平顶，灯全藏进去了，头顶不见光源，光却铺得很匀。

地面是很浅的哑光面，光落上去不晃眼睛，走一路都不刺眼☁️

沙发背景墙没做任何堆砌，整片深灰衬着一组黑色沙发，安静得像没人说话。

走廊一直通到最里面，墙面同色收过去，空间被拉得很长，眼睛一路顺畅不会被打断。

坐下来的那一刻，肩膀和后背都松了，人也不急着开口🧸

这样的空间不靠装饰取胜，墙顶地一气呵成，安静感就出来了。

想看同款安静家，评论区留言「案例」。"""

PATCH = f'''
import os, json, asyncio, asyncpg, re
from yuxi.utils.line_breaks import normalize_published_body
from yuxi.content.generation import (
    case_body_has_craft_content,
    rewrite_cta_comment_keyword,
    trim_cta_to_single_sentence,
    body_has_cta,
)

ARTIFACT_ID = "{ARTIFACT_ID}"
NEW_BODY = {NEW_BODY!r}

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
        old = row["body"] or ""
        body = trim_cta_to_single_sentence(
            rewrite_cta_comment_keyword(normalize_published_body(NEW_BODY), "案例")
        )
        await conn.execute(
            "update content_artifacts set body=$1, updated_at=now() where id=$2",
            body, ARTIFACT_ID,
        )
        paras = [p for p in re.split(r"\\n\\s*\\n", body) if p.strip()]
        print(json.dumps({{
            "title": row["title"],
            "changed": old != body,
            "has_cta": body_has_cta(body),
            "has_craft": case_body_has_craft_content(body),
            "has_case_cta": "留言「案例」" in body,
            "tail": paras[-2:],
        }}, ensure_ascii=False))
    finally:
        await conn.close()

asyncio.run(main())
'''


def run(c, cmd, timeout=200):
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
        with sftp.file("/tmp/_patch_21e40f.py", "w") as f:
            f.write(PATCH)
        sftp.close()
        print(run(c, "python3 -m py_compile /srv/contentswarm/backend/package/yuxi/content/generation.py && echo SYNTAX_OK"))
        patch_out = run(
            c,
            "docker cp /tmp/_patch_21e40f.py api-dev:/tmp/_patch_21e40f.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_patch_21e40f.py",
            timeout=55,
        )
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
            "timeout 200 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_direct_content_generation.py "
            "-k 'case_body_detects or case_share_strips or case_share_generation_keeps "
            "or case_share_rewrites or uses_only_requested' -q --tb=line",
            timeout=220,
        )
        OUT.write_text(patch_out + "\n===pytest===\n" + pytest_out, encoding="utf-8")
        print(patch_out.encode("ascii", "replace").decode("ascii"))
        print(pytest_out[-1100:].encode("ascii", "replace").decode("ascii"))
        ok_patch = (
            '"has_craft": false' in patch_out
            and '"has_case_cta": true' in patch_out
            and '"has_cta": true' in patch_out
        )
        ok_test = "passed" in pytest_out and "failed" not in pytest_out
        return 0 if ok_patch and ok_test else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
