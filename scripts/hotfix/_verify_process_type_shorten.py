#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
LOCAL = Path(__file__).resolve().parents[2] / "backend/package/yuxi/content/control/visual_template_fields.py"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE_CHECK = r'''
from yuxi.content.control.visual_template_fields import resolve_hycanvas_template_fields

fields = resolve_hycanvas_template_fields(
    [{"kind": "text", "key": "field_1", "label": "防臭防堵", "semanticRole": "process_type", "constraints": {}}],
    visual_text=["t", "s"],
    brief={"business_variables": {"工艺类型": "水电施工与隐蔽验收"}},
)
assert fields["field_1"] == "水电施工", fields
fields2 = resolve_hycanvas_template_fields(
    [{"kind": "text", "key": "field_1", "label": "工艺", "semanticRole": "process_type", "constraints": {"maxChars": 4}}],
    visual_text=["t", "s"],
    brief={"business_variables": {"工艺类型": "油漆施工验收"}},
)
assert fields2["field_1"] == "油漆施工", fields2
fields3 = resolve_hycanvas_template_fields(
    [{"kind": "text", "key": "field_1", "label": "工艺", "semanticRole": "process_type", "constraints": {}}],
    visual_text=["t", "s"],
    brief={"business_variables": {"工艺类型": "泥瓦施工"}},
)
assert fields3["field_1"] == "泥瓦施工", fields3
print("OK", fields, fields2, fields3)
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
        sftp.put(str(LOCAL), "/tmp/visual_template_fields.py")
        with sftp.file("/tmp/_check_process_shorten.py", "w") as f:
            f.write(REMOTE_CHECK)
        sftp.close()
        out = run(
            c,
            "docker cp /tmp/visual_template_fields.py api-dev:/tmp/visual_template_fields.py && "
            "docker cp /tmp/_check_process_shorten.py api-dev:/tmp/_check_process_shorten.py && "
            "docker exec api-dev sh -c '"
            "cp /tmp/visual_template_fields.py /app/package/yuxi/content/control/visual_template_fields.py && "
            "python /tmp/_check_process_shorten.py'",
            timeout=90,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out)
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
