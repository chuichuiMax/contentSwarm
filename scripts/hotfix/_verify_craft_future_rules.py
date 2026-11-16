#!/usr/bin/env python3
"""Verify future-generation craft/title/CTA rules are live on 103.15."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PROBE = r'''
from yuxi.content.generation import _direct_generation_prompt, is_craft_content
from yuxi.content.title_rules import title_needs_rewrite

assert is_craft_content("CT06", "工艺施工展示")
assert title_needs_rewrite("整户188㎡做完整面木饰面，这工艺")
assert not title_needs_rewrite("木饰面接缝不齐？错缝才靠谱")

prompt = _direct_generation_prompt(
    style_block="",
    viral_source={"title": "", "body": ""},
    user_request='{"contentType":{"contentTypeCode":"CT06","typeName":"工艺施工展示"},"businessVariables":{"所在区域":"长沙","外框面积":"188"}}',
    generation_prompt="写木饰面工艺",
    forbidden_lexicon={},
    knowledge_context="",
    image_attached=True,
)
checks = {
    "cta": "正文最后一段写一句引流引导" in prompt,
    "title_smooth": "标题要顺口" in prompt,
    "craft_image": "图片只用来印证工艺点" in prompt,
    "no_sofa_tv_lecture": "看不到电视机，写沙发背景墙" not in prompt,
    "no_fine_furniture": "不要逐件描写家具、软装、杂志、摆件" in prompt,
    "awkward_example": "做完整面木饰面" in prompt,
}
print(checks)
assert all(checks.values()), checks
print("FUTURE_RULES_OK")
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
    root = Path(__file__).resolve().parents[2]
    files = [
        (
            root / "backend/package/yuxi/content/generation.py",
            "/srv/contentswarm/backend/package/yuxi/content/generation.py",
        ),
        (
            root / "backend/package/yuxi/content/title_rules.py",
            "/srv/contentswarm/backend/package/yuxi/content/title_rules.py",
        ),
        (
            root / "backend/test/unit/content/test_direct_content_generation.py",
            "/srv/contentswarm/backend/test/unit/content/test_direct_content_generation.py",
        ),
    ]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        for local, remote in files:
            sftp.put(str(local), remote)
            print("synced", local.name)
        with sftp.file("/tmp/_probe_future_rules.py", "w") as f:
            f.write(PROBE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_probe_future_rules.py api-dev:/tmp/_probe_future_rules.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_probe_future_rules.py && "
            "timeout 120 docker exec -w /app api-dev uv run --no-sync pytest "
            "test/unit/content/test_direct_content_generation.py::test_craft_image_prompt_avoids_fine_grained_scene_detail "
            "test/unit/content/test_direct_content_generation.py::test_awkward_craft_titles_need_rewrite "
            "test/unit/content/test_direct_content_generation.py::test_image_prompt_no_longer_asks_for_detail_tags "
            "-q --tb=line",
            timeout=150,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii")[-1500:])
        print("OK" if "FUTURE_RULES_OK" in out and "passed" in out else "CHECK_FAILED")
        return 0 if "FUTURE_RULES_OK" in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
