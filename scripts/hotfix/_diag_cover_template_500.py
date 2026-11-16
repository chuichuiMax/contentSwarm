#!/usr/bin/env python3
"""Reproduce cover_template 500 on 172.16.103.15 and print traceback."""
from __future__ import annotations

import json
import os
import sys

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = "/srv/contentswarm"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if err.strip() and err not in out:
        return (out + "\n" + err).strip()
    return out.strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        print("=== env creds (masked) ===")
        print(
            run(
                c,
                f"python3 - <<'PY'\n"
                f"from pathlib import Path\n"
                f"text=Path('{ROOT}/.env').read_text(encoding='utf-8', errors='replace')\n"
                f"keys={{}}\n"
                f"for line in text.splitlines():\n"
                f"    if '=' not in line or line.strip().startswith('#'): continue\n"
                f"    k,v=line.split('=',1)\n"
                f"    k=k.strip(); v=v.strip().strip('\"').strip(\"'\")\n"
                f"    if k in ('ADMIN_USERNAME','ADMIN_PASSWORD','DEFAULT_ADMIN_USERNAME','DEFAULT_ADMIN_PASSWORD','YUXI_ADMIN_USERNAME','YUXI_ADMIN_PASSWORD','USERNAME','PASSWORD'):\n"
                f"        keys[k]=v\n"
                f"print({{k: ('***' if 'PASS' in k else v) for k,v in keys.items()}})\n"
                f"Path('/tmp/_cs_env_keys.json').write_text(__import__('json').dumps(keys), encoding='utf-8')\n"
                f"PY",
            )
        )

        # Login then call endpoint
        script = r'''
import json, urllib.request, urllib.error, ssl
from pathlib import Path
keys=json.loads(Path("/tmp/_cs_env_keys.json").read_text(encoding="utf-8"))
# guess username/password pairs from common env keys
user = keys.get("ADMIN_USERNAME") or keys.get("DEFAULT_ADMIN_USERNAME") or keys.get("YUXI_ADMIN_USERNAME") or "admin"
pwd = keys.get("ADMIN_PASSWORD") or keys.get("DEFAULT_ADMIN_PASSWORD") or keys.get("YUXI_ADMIN_PASSWORD") or ""
print("login_user", user, "pwd_len", len(pwd))

def req(url, data=None, headers=None, method=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    body = None if data is None else json.dumps(data).encode("utf-8")
    r = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if body else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()

# try several login endpoints
token = None
for path, payload in [
    ("/api/auth/login", {"username": user, "password": pwd}),
    ("/api/auth/token", {"username": user, "password": pwd}),
    ("/api/user/login", {"username": user, "password": pwd}),
]:
    code, raw = req("http://127.0.0.1:5050" + path, payload)
    print("try", path, code, raw[:200])
    if code == 200:
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        token = obj.get("access_token") or obj.get("token") or (obj.get("data") or {}).get("access_token")
        if token:
            print("got_token", True)
            break

url = "http://127.0.0.1:5050/api/material-library/items?material_type=cover_template&page=1&page_size=100&sort=newest"
headers = {"Authorization": f"Bearer {token}"} if token else {}
code, raw = req(url, headers=headers)
print("items_api", code)
print(raw[:1500].decode("utf-8", "replace"))

code2, raw2 = req(
    "http://127.0.0.1:8080/api/material-library/items?material_type=cover_template&page=1&page_size=100&sort=newest",
    headers=headers,
)
print("items_8080", code2)
print(raw2[:800].decode("utf-8", "replace"))
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_repro_ct500.py", "w") as f:
            f.write(script)
        sftp.close()
        print("=== repro ===")
        print(run(c, "python3 /tmp/_repro_ct500.py", timeout=90))

        print("=== api logs (recent errors) ===")
        print(
            run(
                c,
                "docker logs api-dev --since 5m 2>&1 | tail -n 120",
                timeout=60,
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
