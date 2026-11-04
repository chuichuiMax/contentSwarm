"""List HyCanvas workspace fonts on 103.15."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
API = "http://127.0.0.1:8005"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


REMOTE = r'''
import json, subprocess, urllib.parse, urllib.request, http.cookiejar

API = "http://127.0.0.1:8005"
WS = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_WORKSPACE_ID"], text=True).strip()
KEY = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_API_KEY"], text=True).strip()
print("workspace", WS)

def opener_from_ticket():
    req = urllib.request.Request(
        f"{API}/api/v1/auth/integration-ticket/workspace/{WS}",
        method="POST",
        headers={"Authorization": f"Bearer {KEY}"},
    )
    ticket = json.loads(urllib.request.urlopen(req, timeout=60).read())["ticket"]
    redeem = (
        f"{API}/api/v1/auth/integration?ticket={urllib.parse.quote(ticket)}"
        f"&workspaceId={WS}&next=%2Fdashboard%2F"
    )
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(redeem, timeout=60)
    return op

def fetch(qs):
    op = opener_from_ticket()
    with op.open(f"{API}/api/v1/workspaces/{WS}/assets{qs}", timeout=120) as resp:
        return json.loads(resp.read())

for label, qs in [("tag_oc_font", "?tag=oc-font"), ("all", "")]:
    arr = fetch(qs)
    fonts = [a for a in arr if (a.get("kind") or "").lower() == "font"]
    print(f"\n=== {label} total={len(arr)} fonts={len(fonts)} ===")
    for a in sorted(fonts, key=lambda x: x.get("createdAt") or ""):
        fn = a.get("filename") or ""
        tags = a.get("tags") or []
        print(a["id"][:8], "size", a.get("byteSize"), "tags", tags, "fn_repr", repr(fn))
        if "乐" in fn or "波波" in fn or "lem" in fn.lower():
            print("  ^ match lemi")

needle = "乐米"
for a in fetch(""):
    fn = a.get("filename") or ""
    if needle in fn or "lem" in fn.lower():
        print("found by filename:", a)
'''


def main() -> None:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=_pw(), timeout=30)
    path = "/tmp/_list_10315_fonts.py"
    sftp = c.open_sftp()
    with sftp.file(path, "w") as f:
        f.write(REMOTE)
    sftp.close()
    _, o, e = c.exec_command(f"python3 {path}", timeout=180)
    print(o.read().decode("utf-8", "replace"))
    err = e.read().decode("utf-8", "replace")
    if err:
        print(err, file=sys.stderr)
    c.close()


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
