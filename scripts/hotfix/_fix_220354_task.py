#!/usr/bin/env python3
"""Deploy craft/title/CTA fixes and patch ct_220354... on 103.15."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().with_suffix(".out.txt")

FILES = [
    (
        ROOT / "backend/package/yuxi/content/generation.py",
        "/srv/contentswarm/backend/package/yuxi/content/generation.py",
    ),
    (
        ROOT / "backend/package/yuxi/content/title_rules.py",
        "/srv/contentswarm/backend/package/yuxi/content/title_rules.py",
    ),
    (
        ROOT / "backend/test/unit/content/test_direct_content_generation.py",
        "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py",
    ),
]

ARTIFACT_ID = "ca_0a0de976be934a72a27a2e8bde2a3c36"
NEW_TITLE = "木饰面接缝不齐？错缝才靠谱🔥"
NEW_BODY = """整户面积大，大面积木饰面更考验手艺😭

看面上是深色木饰面一门通顶，拼得齐不齐，一眼见高低。

木饰面接缝对上石膏板缝，容易沿着缝开裂。

我们现场就是石膏板接缝先错开，不留通缝。

一排柜门从地到顶，上下缝隙要顺。

柜门没装，先要核对柜体收口尺寸。

收口全靠背板找平，靠墙那道缝才小。

进场验收时，对着这篇把柜门缝和板缝看一遍。

评论区留言「木饰面」，发你一份接缝验收对照要点。"""

PATCH = f'''
import os, json, asyncio, asyncpg
ARTIFACT_ID = "{ARTIFACT_ID}"
NEW_TITLE = {NEW_TITLE!r}
NEW_BODY = {NEW_BODY!r}

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
            "select title, right(body,120) as tail from content_artifacts where id=$1",
            ARTIFACT_ID,
        )
        print(json.dumps({{"title": row["title"], "tail": row["tail"]}}, ensure_ascii=False))
    finally:
        await conn.close()

asyncio.run(main())
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
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
        with sftp.file("/tmp/_patch_220.py", "w") as f:
            f.write(PATCH)
        sftp.close()
        print(run(c, "python3 -m py_compile /srv/contentswarm/backend/package/yuxi/content/generation.py /srv/contentswarm/backend/package/yuxi/content/title_rules.py && echo SYNTAX_OK"))
        print("=== patch artifact ===")
        patch_out = run(
            c,
            "docker cp /tmp/_patch_220.py api-dev:/tmp/_patch_220.py && "
            "timeout 30 docker exec -w /app api-dev python /tmp/_patch_220.py",
            timeout=45,
        )
        OUT.write_text(patch_out + "\n", encoding="utf-8")
        print("patched", "木饰面接缝" in patch_out, "引流" in patch_out or "留言" in patch_out)
        for i in range(15):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print("health", i, code)
            if code.endswith("200"):
                break
            time.sleep(2)
        print("=== pytest ===")
        out = run(
            c,
            "timeout 120 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_direct_content_generation.py -q --tb=line",
            timeout=140,
        )
        OUT.write_text(OUT.read_text(encoding="utf-8") + "\n===pytest===\n" + out, encoding="utf-8")
        print("pytest_tail_ok", "passed" in out[-400:])
        print(out[-800:].encode("ascii", "replace").decode("ascii"))
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
