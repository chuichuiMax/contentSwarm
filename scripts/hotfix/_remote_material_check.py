"""Upload and run material API check on 103.15."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = "172.16.103.15"
ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"
REMOTE = "/tmp/_material_api_check.py"

SCRIPT = r'''
import json
import urllib.error
import urllib.request


def load_admin_creds():
    env = {}
    with open("/srv/contentswarm/.env", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    user = env.get("YUXI_ADMIN_USERNAME") or env.get("ADMIN_USERNAME") or "admin"
    password = env.get("YUXI_ADMIN_PASSWORD") or env.get("ADMIN_PASSWORD") or ""
    return user, password


def main():
    import os
    item_id = os.environ.get("ITEM_ID", "")
    user, password = load_admin_creds()
    if not password:
        print("no admin password")
        return
    login = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:5050/api/auth/login",
        data=login,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        token = json.load(r)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    for path in (
        f"/api/material-library/items/{item_id}/thumbnail",
        f"/api/material-library/items/{item_id}/file",
    ):
        url = "http://127.0.0.1:5050" + path
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
                print(path, r.status, r.headers.get("Content-Type"), len(data))
        except urllib.error.HTTPError as e:
            body = e.read()[:500]
            print(path, "HTTP", e.code, body.decode("utf-8", "replace"))


if __name__ == "__main__":
    main()
'''

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)
sftp = c.open_sftp()
with sftp.file(REMOTE, "w") as f:
    f.write(SCRIPT)
sftp.close()

cmd = f"ITEM_ID={ITEM} python3 {REMOTE}"
_, o, e = c.exec_command(cmd, timeout=180)
out = o.read().decode("utf-8", "replace")
err = e.read().decode("utf-8", "replace")
sys.stdout.buffer.write(out.encode("utf-8", "replace"))
if err.strip():
    sys.stdout.buffer.write(err.encode("utf-8", "replace"))
c.close()
