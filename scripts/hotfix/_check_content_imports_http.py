#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


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


SCRIPT = r'''
python3 <<'PY'
from pathlib import Path
import re, urllib.request
txt = Path('/srv/contentswarm/web/src/views/ContentStudioView.vue').read_text(encoding='utf-8')
imps = re.findall(r"from '(@/[^']+)'", txt)
# also relative imports
imps += re.findall(r"from '(\.[^']+)'", txt)
print('imports', len(imps))
bad = []
for imp in imps:
    if imp.startswith('@/'):
        url = 'http://127.0.0.1:5173/src/' + imp[2:]
    else:
        # skip relative for now
        continue
    # try with extensions
    candidates = [url]
    if not url.endswith(('.vue', '.js', '.ts')):
        candidates = [url + '.js', url + '.vue', url + '.ts', url]
    ok = False
    last = None
    for cand in candidates:
        try:
            with urllib.request.urlopen(cand, timeout=20) as resp:
                body = resp.read(200).decode('utf-8', 'replace')
                if 'Internal Server Error' in body or 'Failed to resolve' in body or 'ENOENT' in body:
                    last = (cand, resp.status, body[:120])
                    continue
                ok = True
                break
        except Exception as e:
            last = (cand, 'ERR', str(e)[:120])
    if not ok:
        bad.append((imp, last))
print('bad_count', len(bad))
for item in bad[:40]:
    print('BAD', item)
PY
'''


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        out = run(c, SCRIPT, timeout=180)
        # also check material audience view transform
        out2 = run(
            c,
            "curl -s -m 20 -o /tmp/ma.js -w '%{http_code} %{size_download}\\n' "
            "'http://127.0.0.1:5173/src/views/MaterialAudienceConfigView.vue'; "
            "python3 -c \"t=open('/tmp/ma.js',encoding='utf-8',errors='replace').read();"
            "print('audience_err', 'Internal Server Error' in t or 'Failed to resolve' in t); print(t[:200])\"",
        )
        # restart web-dev to clear weird vite state then recheck ContentStudio
        out3 = run(
            c,
            "cd /srv/contentswarm && docker compose restart web && sleep 8 && "
            "curl -s -m 20 -o /dev/null -w 'after_restart_cs:%{http_code}\\n' "
            "'http://127.0.0.1:5173/src/views/ContentStudioView.vue' && "
            "curl -s -m 10 -o /dev/null -w 'after_restart_app:%{http_code}\\n' "
            "'http://127.0.0.1:5173/src/main.js'",
            timeout=120,
        )
        text = out + "\n===audience===\n" + out2 + "\n===restart===\n" + out3
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:6000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
