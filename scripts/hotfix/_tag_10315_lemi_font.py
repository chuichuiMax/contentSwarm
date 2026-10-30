"""Tag 乐米波波体 + any untagged workspace fonts with oc-font on 103.15."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
API = "http://127.0.0.1:8005"
TAG = "oc-font"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


REMOTE = f'''
import json, subprocess, urllib.parse, urllib.request, http.cookiejar

API = "{API}"
TAG = "{TAG}"
WS = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_WORKSPACE_ID"], text=True).strip()
KEY = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_API_KEY"], text=True).strip()

req = urllib.request.Request(
    f"{{API}}/api/v1/auth/integration-ticket/workspace/{{WS}}",
    method="POST",
    headers={{"Authorization": f"Bearer {{KEY}}"}},
)
ticket = json.loads(urllib.request.urlopen(req, timeout=60).read())["ticket"]
redeem = (
    f"{{API}}/api/v1/auth/integration?ticket={{urllib.parse.quote(ticket)}}"
    f"&workspaceId={{WS}}&next=%2Fdashboard%2F"
)
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open(redeem, timeout=60)

with op.open(f"{{API}}/api/v1/workspaces/{{WS}}/assets", timeout=120) as resp:
    assets = json.loads(resp.read())

patched = 0
for a in assets:
    if (a.get("kind") or "").lower() != "font":
        continue
    tags = list(a.get("tags") or [])
    if TAG in tags:
        continue
    tags.append(TAG)
    body = json.dumps({{"tags": tags}}).encode()
    preq = urllib.request.Request(
        f"{{API}}/api/v1/assets/{{a['id']}}",
        data=body,
        method="PATCH",
        headers={{"Content-Type": "application/json"}},
    )
    with op.open(preq, timeout=60) as pr:
        pr.read()
    fn = a.get("filename") or ""
    print("tagged", a["id"][:8], fn)
    patched += 1
print("done patched", patched)
'''


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=_pw(), timeout=30)
    path = "/tmp/_tag_fonts.py"
    sftp = c.open_sftp()
    with sftp.file(path, "w") as f:
        f.write(REMOTE)
    sftp.close()
    _, o, e = c.exec_command(f"python3 {path}", timeout=180)
    print(o.read().decode())
    err = e.read().decode()
    if err.strip():
        print(err, file=sys.stderr)
    return o.channel.recv_exit_status()


if __name__ == "__main__":
    raise SystemExit(main())
