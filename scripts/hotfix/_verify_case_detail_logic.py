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
from yuxi.content import generation as g

detailed = (
    "坐在餐桌前，脖子先松下来了🍃\n\n"
    "上面挂着一块深色木块拼的墙面，一块一块错着落。\n\n"
    "头顶两只纸灯笼亮着，光是散的，照到我肩膀上很软。\n\n"
    "桌面木纹摸起来是温的，手臂搭上去不想挪。\n\n"
    "想看同款思路就留言「案例」。"
)
simple = "一进门心里就松了🍃\n\n暖调木作墙很安静，吃饭时也踏实😌\n\n想看同款思路就留言「案例」。"
assert g.case_body_is_over_detailed(detailed), "detailed should hit"
assert not g.case_body_is_over_detailed(simple), "simple should pass"

prompt = g._direct_generation_prompt(
    style_block="",
    viral_source={"title": "", "body": ""},
    user_request='{"contentType":{"contentTypeCode":"CT01","typeName":"装修案例分享"}}',
    generation_prompt="结合图片中的具体设计元素写",
    forbidden_lexicon={},
    knowledge_context="",
    image_attached=True,
)
assert "图片只用来感受空间氛围和整体风格" in prompt
assert "即使用户生成提示词要求结合具体设计元素" in prompt
assert "不要逐件描写构件拼法、灯具数量、桌面触感" in prompt
assert "只写提示词要求、并且图片里能看到的内容" not in prompt
print("LOGIC_OK")
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
        raise TimeoutError(cmd[:80])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_verify_case_detail.py", "w") as f:
            f.write(REMOTE)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/_verify_case_detail.py api-dev:/tmp/_verify_case_detail.py && "
            "timeout 40 docker exec -w /app api-dev python /tmp/_verify_case_detail.py",
        )
        OUT.write_text(out, encoding="utf-8")
        print(out)
        return 0 if "LOGIC_OK" in out else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
