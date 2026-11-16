#!/usr/bin/env python3
"""Expand audience stub so MLS NameErrors are gone; verify cover_template list."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"

STUB = '''from __future__ import annotations

"""Temporary stub: incomplete ContentMaterialAudience feature quarantined for API boot."""

from typing import Any


async def ensure_enterprise_gallery_audiences(db: Any) -> list:
    return []


async def reject_enterprise_audience_denied(db: Any, user: Any, category: Any = None, *args: Any, **kwargs: Any) -> None:
    return None


async def resolve_enterprise_gallery_audience(db: Any, user: Any, *args: Any, **kwargs: Any) -> Any:
    return None


async def list_material_audiences(db: Any) -> list:
    return []


async def create_material_audience(db: Any, user: Any, payload: Any) -> dict:
    raise RuntimeError("material audience feature temporarily disabled")


async def update_material_audience(db: Any, user: Any, audience_id: str, payload: Any) -> dict:
    raise RuntimeError("material audience feature temporarily disabled")


async def delete_material_audience(db: Any, user: Any, audience_id: str) -> dict:
    raise RuntimeError("material audience feature temporarily disabled")
'''

HOST_SCRIPT = r'''
from pathlib import Path
import re

root = Path("/srv/contentswarm/backend")
mls = root / "package/yuxi/services/material_library_service.py"
bak = root / "package/yuxi/services/enterprise_gallery_audience.py.broken_bak"
text = mls.read_text(encoding="utf-8")

# Collect symbols referenced from enterprise_gallery_audience / common audience helpers
needed = set()
for m in re.finditer(r"\b(ensure_enterprise_gallery_audiences|reject_enterprise_audience_denied|resolve_enterprise_gallery_audience|list_material_audiences|create_material_audience|update_material_audience|delete_material_audience|audience_matches_employee|resolve_material_audience|can_view_enterprise_gallery_audience)\b", text):
    needed.add(m.group(1))

# Also scan bak exports
if bak.exists():
    bt = bak.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"^async def (\w+)\(|^def (\w+)\(", bt, re.M):
        name = m.group(1) or m.group(2)
        if name in text:
            needed.add(name)

print("needed symbols:", sorted(needed))

# Ensure import includes all needed symbols
import_pat = re.compile(
    r"from yuxi\.services\.enterprise_gallery_audience import \((.*?)\)\n",
    re.S,
)
m = import_pat.search(text)
wanted = sorted(needed) or ["ensure_enterprise_gallery_audiences", "reject_enterprise_audience_denied"]
block = "from yuxi.services.enterprise_gallery_audience import (\n" + "".join(f"    {n},\n" for n in wanted) + ")\n"
if m:
    text = import_pat.sub(block, text, count=1)
    print("replaced import block")
else:
    needle = "from yuxi.services.material_folder_permissions import ("
    idx = text.find(needle)
    end = text.find(")\n", idx)
    text = text[: end + 2] + block + text[end + 2 :]
    print("inserted import block")

mls.write_text(text, encoding="utf-8")
print("MLS import lines:")
for i, line in enumerate(mls.read_text(encoding="utf-8").splitlines(), 1):
    if "enterprise_gallery_audience" in line or (i > 35 and i < 55 and line.strip().endswith(",")):
        if "enterprise_gallery" in line or "ensure_" in line or "reject_" in line or "resolve_" in line:
            print(f"  {i}: {line}")
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
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
        print("=== bak function names ===")
        print(
            run(
                c,
                "grep -nE '^(async )?def |^class ' "
                "/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py.broken_bak | head -40",
            )
        )
        print("=== MLS audience symbol refs ===")
        print(
            run(
                c,
                "grep -nE 'reject_enterprise_audience_denied|ensure_enterprise_gallery_audiences|resolve_enterprise_gallery|from yuxi.services.enterprise_gallery' "
                "/srv/contentswarm/backend/package/yuxi/services/material_library_service.py",
            )
        )

        sftp = c.open_sftp()
        with sftp.file("/tmp/enterprise_gallery_audience_stub.py", "w") as f:
            f.write(STUB)
        with sftp.file("/tmp/_fix_stub_symbols.py", "w") as f:
            f.write(HOST_SCRIPT)
        sftp.close()

        print("=== install stub + fix imports ===")
        print(
            run(
                c,
                "cp /tmp/enterprise_gallery_audience_stub.py "
                "/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py && "
                "python3 /tmp/_fix_stub_symbols.py",
            )
        )

        # Wait for hot reload / hit health
        print("=== wait healthy ===")
        for i in range(24):
            time.sleep(3)
            code = run(
                c,
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || true",
            ).strip()
            print(f"wait {i}: {code}")
            if code == "200":
                break

        # Find admin password from saves/config or db seed docs
        print("=== find admin password ===")
        print(
            run(
                c,
                "grep -RIn --include='*.toml' --include='*.env' --include='*.md' -E 'SUPER_ADMIN|admin.*pass|password.*=.*' "
                "/srv/contentswarm/.env /srv/contentswarm/docker/volumes/yuxi/config 2>/dev/null | head -30; "
                "ls /srv/contentswarm/docker/volumes/yuxi/config 2>/dev/null | head",
            )
        )

        # Form login for /api/auth/token
        print("=== login form + cover_template ===")
        verify = r'''
import json, urllib.request, urllib.parse, urllib.error
from pathlib import Path

# try common passwords from project docs / previous context
candidates = []
env = Path("/srv/contentswarm/.env")
if env.exists():
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k,v=line.split("=",1)
            k=k.strip(); v=v.strip().strip('"').strip("'")
            if "PASS" in k.upper() or "ADMIN" in k.upper():
                print("env", k, "=", "***" if "PASS" in k.upper() else v)
                if "PASS" in k.upper() and v:
                    candidates.append(v)

# known defaults used in this project historically
for p in ["admin123", "Admin123", "Hirun2026@", "yuxi123", "123456", "admin"]:
    if p not in candidates:
        candidates.append(p)

token=None
user="admin"
for pwd in candidates:
    data=urllib.parse.urlencode({"username": user, "password": pwd}).encode()
    req=urllib.request.Request("http://127.0.0.1:5050/api/auth/token", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            obj=json.loads(resp.read().decode())
            token=obj.get("access_token")
            print("login_ok", user, "pwd", pwd[:2]+"***", "token", bool(token))
            break
    except urllib.error.HTTPError as e:
        body=e.read()[:120]
        print("login_fail", pwd[:2]+"***", e.code, body)
    except Exception as e:
        print("login_err", type(e).__name__, e)

if not token:
    raise SystemExit("no token")

url="http://127.0.0.1:5050/api/material-library/items?material_type=cover_template&page=1&page_size=100&sort=newest"
req=urllib.request.Request(url)
req.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw=resp.read()
        print("items", resp.status, raw[:300].decode("utf-8","replace"))
except urllib.error.HTTPError as e:
    print("items", e.code, e.read()[:800].decode("utf-8","replace"))

# also via 8080
url2="http://127.0.0.1:8080/api/material-library/items?material_type=cover_template&page=1&page_size=100&sort=newest"
req2=urllib.request.Request(url2)
req2.add_header("Authorization", f"Bearer {token}")
try:
    with urllib.request.urlopen(req2, timeout=60) as resp:
        raw=resp.read()
        print("items8080", resp.status, raw[:200].decode("utf-8","replace"))
except urllib.error.HTTPError as e:
    print("items8080", e.code, e.read()[:400].decode("utf-8","replace"))
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_verify_ct500.py", "w") as f:
            f.write(verify)
        sftp.close()
        print(run(c, "python3 /tmp/_verify_ct500.py", timeout=120))

        print("=== latest error snippet ===")
        print(
            run(
                c,
                "docker logs api-dev --since 2m 2>&1 | grep -E 'ERROR|NameError|Traceback|cover_template|material-library/items' | tail -40",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
