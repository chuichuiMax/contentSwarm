"""Fetch api-dev logs for brief errors and probe brief endpoint on 103.15."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
TASK = "ct_a020bad049274045ac580a5aefd4252d"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    p = Path(__file__).resolve().parents[2] / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=_pw(), timeout=30)

    remote = f'''
import json, subprocess, urllib.error, urllib.request

TASK = "{TASK}"
BASE = "http://127.0.0.1:5050"

print("=== recent api logs (brief/task) ===")
subprocess.run(
    "docker logs api-dev --tail 400 2>&1 | grep -F ct_a020bad | tail -20",
    shell=True,
)
subprocess.run(
    "docker logs api-dev --tail 400 2>&1 | grep -i brief | tail -15",
    shell=True,
)

print("\\n=== methods without auth ===")
for method in ("GET", "PUT", "POST"):
    req = urllib.request.Request(f"{{BASE}}/api/content/tasks/{{TASK}}/brief", method=method, data=b'{{"brief":{{}}}}' if method != "GET" else None)
    if method != "GET":
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(method, r.status, r.read()[:200])
    except urllib.error.HTTPError as e:
        print(method, e.code, e.read()[:400].decode("utf-8", "replace"))

print("\\n=== login + PUT brief (minimal) ===")
# read admin from env in container
env = subprocess.check_output(["docker", "exec", "api-dev", "printenv"], text=True, errors="replace")
user = pwd = None
for line in env.splitlines():
    if line.startswith("YUXI_SUPER_ADMIN_USERNAME="):
        user = line.split("=", 1)[1].strip()
    if line.startswith("YUXI_SUPER_ADMIN_PASSWORD="):
        pwd = line.split("=", 1)[1].strip()
if not user or not pwd:
    print("no super admin env in api-dev")
else:
    login_body = json.dumps({{"username": user, "password": pwd}}).encode()
    req = urllib.request.Request(f"{{BASE}}/api/auth/login", data=login_body, method="POST", headers={{"Content-Type": "application/json"}})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read().decode()).get("access_token") or json.loads(r.read() if False else "{{}}")
    # retry parse
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{{BASE}}/api/auth/login",
            data=login_body,
            method="POST",
            headers={{"Content-Type": "application/json"}},
        ),
        timeout=30,
    ) as r:
        login = json.loads(r.read().decode())
    token = login.get("access_token") or login.get("token")
    print("login keys", list(login.keys())[:8])
    brief = {{"business_variables": {{}}, "form_values": {{}}}}
    body = json.dumps({{"brief": brief}}).encode()
    req = urllib.request.Request(
        f"{{BASE}}/api/content/tasks/{{TASK}}/brief",
        data=body,
        method="PUT",
        headers={{"Content-Type": "application/json", "Authorization": f"Bearer {{token}}"}},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print("PUT", r.status, r.read()[:800].decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print("PUT", e.code, e.read()[:1200].decode("utf-8", "replace"))
'''
    path = "/tmp/_debug_brief.py"
    sftp = c.open_sftp()
    with sftp.file(path, "w") as f:
        f.write(remote)
    sftp.close()
    _, o, e = c.exec_command(f"python3 {path}", timeout=120)
    print(o.read().decode("utf-8", "replace"))
    err = e.read().decode("utf-8", "replace")
    if err.strip():
        print(err, file=sys.stderr)
    c.close()


if __name__ == "__main__":
    main()
