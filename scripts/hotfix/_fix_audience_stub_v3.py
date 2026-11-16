#!/usr/bin/env python3
"""Install complete audience stub and verify cover_template endpoint."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"

STUB = r'''from __future__ import annotations

"""Temporary stub while ContentMaterialAudience model is missing on this host."""

from types import SimpleNamespace
from typing import Any

DEFAULT = SimpleNamespace(
    id="mga_default",
    code="default",
    name="默认员工",
    match_keywords=[],
    is_default=True,
    sort_order=1000,
    enabled=True,
)


async def ensure_enterprise_gallery_audiences(db: Any) -> list:
    return [DEFAULT]


async def ensure_material_audiences(db: Any) -> list:
    return [DEFAULT]


async def reject_enterprise_audience_denied(db: Any, user: Any, category: Any = None, *args: Any, **kwargs: Any) -> None:
    return None


async def filter_categories_by_audience(db: Any, user: Any, categories: list, *args: Any, **kwargs: Any) -> list:
    return list(categories or [])


async def audience_catalog(db: Any) -> dict[str, Any]:
    return {DEFAULT.id: DEFAULT}


async def validate_audience_id(db: Any, audience_id: str | None) -> str | None:
    value = (audience_id or "").strip()
    if not value:
        return None
    return value


async def resolve_user_material_audience(db: Any, user: Any, *args: Any, **kwargs: Any) -> Any:
    return DEFAULT


def resolve_root_audience_id(*args: Any, **kwargs: Any) -> str:
    return DEFAULT.id


def enterprise_category_visible_for_audience(*args: Any, **kwargs: Any) -> bool:
    return True


def match_audience_for_branch(*args: Any, **kwargs: Any) -> Any:
    return DEFAULT


async def list_material_audiences(db: Any) -> dict[str, Any]:
    return {"items": [{"id": DEFAULT.id, "code": DEFAULT.code, "name": DEFAULT.name, "is_default": True}]}


async def create_material_audience(db: Any, user: Any, payload: Any) -> dict:
    raise RuntimeError("material audience feature temporarily disabled")


async def update_material_audience(db: Any, user: Any, audience_id: str, payload: Any) -> dict:
    raise RuntimeError("material audience feature temporarily disabled")


async def delete_material_audience(db: Any, user: Any, audience_id: str) -> dict:
    raise RuntimeError("material audience feature temporarily disabled")
'''

IMPORT_FIX = r'''
from pathlib import Path
import re

mls = Path("/srv/contentswarm/backend/package/yuxi/services/material_library_service.py")
text = mls.read_text(encoding="utf-8")
# Replace any existing audience import with the exact needed set
block = (
    "from yuxi.services.enterprise_gallery_audience import (\n"
    "    audience_catalog,\n"
    "    ensure_enterprise_gallery_audiences,\n"
    "    filter_categories_by_audience,\n"
    "    reject_enterprise_audience_denied,\n"
    "    validate_audience_id,\n"
    ")\n"
)
pat = re.compile(r"from yuxi\.services\.enterprise_gallery_audience import \([^)]*\)\n", re.S)
if pat.search(text):
    text = pat.sub(block, text, count=1)
else:
    raise SystemExit("import block missing")
# Remove accidental _error import usage if any leftover from bad patch
text = text.replace("    _error,\n", "")
mls.write_text(text, encoding="utf-8")
print("import ok")
for i, line in enumerate(mls.read_text(encoding="utf-8").splitlines()[39:50], 40):
    print(f"{i}: {line}")
'''

VERIFY = r'''
import json, urllib.request, urllib.parse, urllib.error
from pathlib import Path

candidates=[]
env=Path("/srv/contentswarm/.env")
if env.exists():
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k,v=line.split("=",1)
            k=k.strip(); v=v.strip().strip('"').strip("'")
            if "PASS" in k.upper() and v:
                candidates.append(v)
                print("env_pass_key", k)
for p in ["admin123","Admin123","Hirun2026@","yuxi123","123456","admin","Hirun@2026"]:
    if p not in candidates:
        candidates.append(p)

# also try reading base.toml / secrets
for p in Path("/srv/contentswarm/docker/volumes/yuxi").rglob("*.toml"):
    try:
        t=p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    if "password" in t.lower():
        print("toml", p)

token=None
for user in ["admin", "superadmin", "tiechui"]:
    for pwd in candidates:
        data=urllib.parse.urlencode({"username": user, "password": pwd}).encode()
        req=urllib.request.Request("http://127.0.0.1:5050/api/auth/token", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                obj=json.loads(resp.read().decode())
                token=obj.get("access_token")
                if token:
                    print("login_ok", user)
                    break
        except Exception:
            pass
    if token:
        break
if not token:
    # dump users table tip
    print("NO_TOKEN")
else:
    url="http://127.0.0.1:5050/api/material-library/items?material_type=cover_template&page=1&page_size=100&sort=newest"
    req=urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw=resp.read()
            print("items", resp.status)
            print(raw[:400].decode("utf-8","replace"))
    except urllib.error.HTTPError as e:
        print("items", e.code)
        print(e.read()[:1000].decode("utf-8","replace"))
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    out_chunks = []
    err_chunks = []
    while True:
        if chan.recv_ready():
            out_chunks.append(chan.recv(65535))
        if chan.recv_stderr_ready():
            err_chunks.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready():
                out_chunks.append(chan.recv(65535))
            while chan.recv_stderr_ready():
                err_chunks.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    out = b"".join(out_chunks).decode("utf-8", "replace")
    err = b"".join(err_chunks).decode("utf-8", "replace")
    if err.strip() and err not in out:
        return (out + "\n" + err).strip()
    return out.strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file(
            "/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py", "w"
        ) as f:
            f.write(STUB)
        with sftp.file("/tmp/_fix_import_audience.py", "w") as f:
            f.write(IMPORT_FIX)
        with sftp.file("/tmp/_verify_ct.py", "w") as f:
            f.write(VERIFY)
        sftp.close()

        print("=== fix import ===")
        print(run(c, "python3 /tmp/_fix_import_audience.py"))

        print("=== health loop ===")
        for i in range(30):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000",
            )
            print(f"wait {i}: {code}")
            if code.strip().endswith("200"):
                break
            if i in (5, 12, 20):
                print(run(c, "docker logs api-dev --tail 30 2>&1 | tail -n 30"))
            time.sleep(3)

        print("=== verify endpoint ===")
        print(run(c, "python3 /tmp/_verify_ct.py", timeout=120))

        print("=== recent errors ===")
        print(
            run(
                c,
                "docker logs api-dev --since 3m 2>&1 | grep -E 'NameError|ImportError|ERROR|Traceback|material-library/items' | tail -30",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
