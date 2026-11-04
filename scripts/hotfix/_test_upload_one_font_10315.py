"""Upload one local font to 103.15 HyCanvas workspace (browser session)."""
from __future__ import annotations

import base64
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
FONT = Path(r"c:\Users\10533\Desktop\字体包_extracted\字体包\乐米波波体.ttf")


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    if not FONT.is_file():
        print("missing", FONT, file=sys.stderr)
        return 1
    data = FONT.read_bytes()
    b64 = base64.b64encode(data).decode()
    remote_py = f"""
import base64, json, subprocess, sys, urllib.parse, urllib.request, http.cookiejar

API = "{API}"
WS = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_WORKSPACE_ID"], text=True).strip()
KEY = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_API_KEY"], text=True).strip()
FILENAME = {json.dumps(FONT.name)}
B64 = {json.dumps(b64)}

req = urllib.request.Request(
    f"{{API}}/api/v1/auth/integration-ticket/workspace/{{WS}}",
    method="POST",
    headers={{"Authorization": f"Bearer {{KEY}}"}},
)
with urllib.request.urlopen(req, timeout=60) as resp:
    ticket = json.loads(resp.read().decode())["ticket"]
redeem = (
    f"{{API}}/api/v1/auth/integration?ticket={{urllib.parse.quote(ticket)}}"
    f"&workspaceId={{WS}}&next=%2Fdashboard%2F"
)
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.open(redeem, timeout=60)
body = json.dumps({{"filename": FILENAME, "dataBase64": B64}}).encode()
ureq = urllib.request.Request(
    f"{{API}}/api/v1/workspaces/{{WS}}/assets",
    data=body,
    method="POST",
    headers={{"Content-Type": "application/json"}},
)
try:
    with opener.open(ureq, timeout=300) as resp:
        raw = resp.read().decode()
        print("status", resp.status)
        print(raw[:500])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:800])
    sys.exit(1)
"""
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=_pw(), timeout=30)
    remote = "/tmp/_test_one_font_upload.py"
    sftp = c.open_sftp()
    with sftp.file(remote, "w") as f:
        f.write(remote_py)
    sftp.close()
    _, o, e = c.exec_command(f"python3 {remote}", timeout=360)
    print(o.read().decode())
    err = e.read().decode()
    if err:
        print(err, file=sys.stderr)
    code = o.channel.recv_exit_status()
    c.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
