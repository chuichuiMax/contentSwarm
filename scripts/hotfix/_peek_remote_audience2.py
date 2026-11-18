#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = os.path.splitext(__file__)[0] + ".out.txt"


def run(c, cmd, timeout=50):
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


CMD = r'''
python3 - <<'PY'
import os
root='/srv/contentswarm/backend/package/yuxi'
hits=[]
for dp, dns, fns in os.walk(root):
    for fn in fns:
        if not fn.endswith('.py'): continue
        path=os.path.join(dp,fn)
        txt=open(path,encoding='utf-8',errors='ignore').read()
        if 'class ContentMaterialCategory' in txt or 'class ContentMaterialAudience' in txt:
            hits.append(path)
            print('FILE', path)
            for name in ('class ContentMaterialCategory','class ContentMaterialAudience'):
                if name in txt:
                    i=txt.index(name)
                    print('---', name, '---')
                    print(txt[i:i+2200])
                    print('===END===')
print('HIT_COUNT', len(hits))
# also show ensure_initial and ensure_enterprise call site
mls='/srv/contentswarm/backend/package/yuxi/services/material_library_service.py'
lines=open(mls,encoding='utf-8').read().splitlines()
for i,l in enumerate(lines,1):
    if 'ensure_enterprise_gallery' in l or 'audience_id' in l or 'def ensure_initial_enterprise' in l:
        print(f'{i}:{l}')
PY
'''


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        text = run(c, CMD, timeout=60)
        open(OUT, "w", encoding="utf-8").write(text)
        print(text.encode("ascii", "replace").decode("ascii")[:5000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
