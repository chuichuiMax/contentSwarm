#!/usr/bin/env python3
"""Verify case-craft rules without pytest (venv may lack pytest after recreate)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

SCRIPT = r'''
import asyncio, json, traceback
from yuxi.content import generation as g

checks = []

def check(name, cond):
    checks.append({"name": name, "ok": bool(cond)})
    if not cond:
        raise AssertionError(name)

try:
    check("detect_shoukou", g.case_body_has_craft_content("篇幅有限，很多收口处没细说。"))
    check("detect_jinchang", g.case_body_has_craft_content("收藏这篇，进场时对着墙顶地挨个比一遍。"))
    check("clean_case_ok", not g.case_body_has_craft_content("一进门脚步声都轻了，沙发背景墙很安静。"))
    prompt = g._direct_generation_prompt(
        style_block="",
        viral_source={"title": "", "body": ""},
        user_request='{"contentType":{"typeName":"装修案例分享","contentTypeCode":"CT01"}}',
        generation_prompt="按案例写",
        forbidden_lexicon={},
        knowledge_context="",
        image_attached=True,
    )
    check("case_rule", "这是装修案例分享" in prompt)
    check("no_craft_rule", "不要写工序、验收、收口节点、进场核对" in prompt)
    check("case_cta", "留言「案例」" in prompt)
    check("no_old_hard_fit", "正文可写硬装类设计元素" not in prompt)

    class Fake:
        def bind_tools(self, schemas, **kwargs):
            return self
        async def ainvoke(self, messages):
            Fake.calls.append(messages)
            if len(Fake.calls) == 1:
                from langchain_core.messages import AIMessage
                return AIMessage(content="", tool_calls=[{
                    "name": "ContentArtifactAIEditOutput",
                    "args": {
                        "title": "原来家安静下来，人就不累了🌙",
                        "body": "一进门脚步声都轻了半截🍃\n\n篇幅有限，很多收口处没细说。\n\n收藏这篇，进场时对着墙顶地挨个比一遍。",
                        "topics": ["现代"],
                    },
                    "id": "1",
                }])
            from langchain_core.messages import AIMessage
            return AIMessage(content="", tool_calls=[{
                "name": "ContentArtifactAIEditOutput",
                "args": {
                    "title": "原来家安静下来，人就不累了🌙",
                    "body": "一进门脚步声都轻了半截🍃\n\n想看同款安静家，评论区留言「案例」。",
                    "topics": ["现代"],
                },
                "id": "2",
            }])
    Fake.calls = []
    g.resolve_chat_model_spec = lambda value: value or "default"
    g.load_chat_model = lambda **kwargs: Fake()
    g.choose_sampling = lambda: {"temperature": 0.4, "top_p": 0.9}

    result = asyncio.run(g.generate_direct_content(
        model_spec="deepseek:deepseek-flash",
        creative_style={},
        viral_source={},
        user_request='{"contentType":{"typeName":"装修案例分享","contentTypeCode":"CT01"}}',
        generation_prompt="按案例写",
    ))
    check("repaired_once", len(Fake.calls) == 2)
    check("no_shoukou", "收口处" not in result.body)
    check("no_jinchang", "进场时" not in result.body)
    check("has_case_cta", "留言「案例」" in result.body)
    check("craft_clean", not g.case_body_has_craft_content(result.body))
    print(json.dumps({"ok": True, "checks": checks, "body": result.body}, ensure_ascii=False))
except Exception as exc:
    traceback.print_exc()
    print(json.dumps({"ok": False, "checks": checks, "error": str(exc)}, ensure_ascii=False))
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
        with sftp.file("/tmp/_verify_21e40f_logic.py", "w") as f:
            f.write(SCRIPT)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_verify_21e40f_logic.py api-dev:/tmp/_verify_21e40f_logic.py && "
            "timeout 90 docker exec -w /app api-dev python /tmp/_verify_21e40f_logic.py",
            timeout=100,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out.encode("ascii", "replace").decode("ascii"))
        return 0 if '"ok": true' in out or '"ok":true' in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
