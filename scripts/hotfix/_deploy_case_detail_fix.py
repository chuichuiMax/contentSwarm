#!/usr/bin/env python3
"""Deploy case-share over-detail fix to 103.15 and update DB creative style prompts."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().with_suffix(".out.txt")

LOCAL_GEN = ROOT / "backend/package/yuxi/content/generation.py"
LOCAL_TEST = ROOT / "backend/test/unit/content/test_direct_content_generation.py"

DB_UPDATE = r'''
import os, asyncio, asyncpg, re

REPLACEMENTS = [
    ("结合图片中的具体设计元素/工艺细节，用大白话解答问题。",
     "结合居住感受和整体氛围用大白话解答问题，画面只点到为止，不要逐件描写构件、灯具、桌面触感。"),
    ("严禁虚构图片中没有的东西。所有设计描述、颜色、材质必须基于图片。",
     "严禁虚构图片中没有的东西。画面只用来感受氛围和整体风格，不要逐件描写构件拼法、灯具数量、桌面触感、摆件细节。"),
    ("用大白话描述图片中的设计细节（配色、布局、收纳等），解释为什么好，像现场给业主讲解。",
     "用大白话写整体亮点和住进去的感觉（配色、布局、收纳点到为止），解释为什么好；不要逐件细描构件、灯具、家具触感。"),
    ("所有设计描述必须基于图片内容（如风格、颜色、家具等）。",
     "整体印象必须基于图片（如风格、色调），不要写成看图清单。"),
    ("介绍图片背后的设计理念以及图中可见的设计元素。",
     "介绍图片背后的设计理念和住进去的感觉；画面元素点到为止，不要逐一展开。"),
    ('设计元素展开**：逐一介绍图片中可见的具体设计元素（颜色、材质、造型、家具等）。每个元素一句话带出它和理念的关联（如"墙面用浅灰乳胶漆，配合无主灯，视觉上把墙往后推，空间显大"）。',
     "整体印象**：最多点到一两处硬装整体印象，并一句话带出和理念的关联；不要逐件描写颜色材质清单、灯具数量或家具触感。"),
    ("设计元素展开**：介绍图片可见的具体元素（颜色、材质、造型、家具等）。每个元素一句话带出和理念的关联。专业术语（如无主灯、木饰面、岩板上墙、错缝拼接等）直接用，不硬翻译。",
     "整体印象**：最多点到一两处硬装整体印象，并一句话带出和理念的关联；不要逐件展开。专业术语可直接用，不硬翻译。"),
    ("所有设计元素必须基于图片内容。理念可以解读，但不能凭空编造图片里不存在的造型、颜色或材质。",
     "整体印象必须基于图片。理念可以解读，但不能凭空编造，也不要逐件细描画面。"),
]

async def main():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            """
            select cs.id, cs.name, cs.prompt, ct.name as type_name, ct.type_code
            from creative_styles cs
            join content_types ct on ct.id = cs.content_type_id
            where ct.type_code = 'CT01' or ct.name like '%案例%'
            """
        )
        print("styles", len(rows))
        updated = 0
        for r in rows:
            prompt = r["prompt"] or ""
            new = prompt
            hits = 0
            for old, repl in REPLACEMENTS:
                if old in new:
                    new = new.replace(old, repl)
                    hits += 1
            if hits and new != prompt:
                await conn.execute("update creative_styles set prompt=$1 where id=$2", new, r["id"])
                updated += 1
                print("updated", r["name"], "hits", hits)
            else:
                print("skip", r["name"], "hits", hits, "len", len(prompt))
        print("UPDATED", updated)
    finally:
        await conn.close()

asyncio.run(main())
'''


def run(c, cmd, timeout=180):
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
        sftp.put(str(LOCAL_GEN), "/srv/contentswarm/backend/package/yuxi/content/generation.py")
        sftp.put(str(LOCAL_TEST), "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py")
        with sftp.file("/tmp/_update_case_style_prompts.py", "w") as f:
            f.write(DB_UPDATE)
        sftp.close()

        parts = []
        parts.append("=== sync into api-dev ===")
        parts.append(
            run(
                c,
                "docker cp /srv/contentswarm/backend/package/yuxi/content/generation.py "
                "api-dev:/app/package/yuxi/content/generation.py && "
                "docker cp /srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py "
                "api-dev:/app/test/unit/content/test_direct_content_generation.py && "
                "echo SYNC_OK",
            )
        )
        parts.append("=== update DB creative styles ===")
        parts.append(
            run(
                c,
                "docker cp /tmp/_update_case_style_prompts.py api-dev:/tmp/_update_case_style_prompts.py && "
                "timeout 60 docker exec -w /app api-dev python /tmp/_update_case_style_prompts.py",
            )
        )
        parts.append("=== pytest targeted ===")
        parts.append(
            run(
                c,
                "timeout 180 docker exec -w /app api-dev python -m pytest "
                "test/unit/content/test_direct_content_generation.py "
                "-k 'over_detailed or case_share_prompt_rejects or case_share_strips or case_share_generation_keeps' "
                "-q --tb=line 2>&1 | tail -n 80",
                timeout=200,
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:9000])
        return 0 if "SYNC_OK" in text else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
