#!/usr/bin/env python3
import os, time, paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@", timeout=20)
py = r'''
from yuxi.utils.line_breaks import normalize_published_body
from yuxi.content.title_rules import apply_title_rules
b = "第一段。\n\n第二段。"
print("norm", repr(normalize_published_body(b)))
print("title", repr(apply_title_rules(normalize_published_body(b))))
# inspect title rules
from yuxi.content import title_rules as tr
for rule in tr.TITLE_RULES:
    before = b
    after = rule.apply(b)
    if after != before:
        print("rule_changed", getattr(rule, "name", rule), repr(after[:40]))
'''
sftp = c.open_sftp()
with sftp.file("/tmp/_probe_nl.py", "w") as f:
    f.write(py)
sftp.close()
chan = c.get_transport().open_session()
chan.settimeout(40)
chan.exec_command("docker cp /tmp/_probe_nl.py api-dev:/tmp/_probe_nl.py && docker exec -w /app api-dev python /tmp/_probe_nl.py")
chunks=[]
import time as _t
end=_t.time()+40
while _t.time()<end:
    if chan.recv_ready(): chunks.append(chan.recv(65535))
    if chan.recv_stderr_ready(): chunks.append(chan.recv_stderr(65535))
    if chan.exit_status_ready():
        while chan.recv_ready(): chunks.append(chan.recv(65535))
        break
    _t.sleep(0.05)
open(r"c:\hirunAI\cowxiao\contentSwarm\scripts\hotfix\_probe_nl.out.txt","w",encoding="utf-8").write(b"".join(chunks).decode("utf-8","replace"))
print("wrote")
c.close()
