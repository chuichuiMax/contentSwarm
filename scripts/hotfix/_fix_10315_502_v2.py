#!/usr/bin/env python3
"""Recover api-dev 502: quarantine incomplete material-audience feature with stubs."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
USER = "root"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = "/srv/contentswarm"

STUB = '''from __future__ import annotations

"""Temporary stub: incomplete ContentMaterialAudience feature quarantined for API boot."""

from typing import Any


async def ensure_enterprise_gallery_audiences(db: Any) -> list:
    return []


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
import shutil

root = Path("/srv/contentswarm/backend")
init = root / "server/routers/__init__.py"
text = init.read_text(encoding="utf-8")
orig = text
text = re.sub(r"^from server\.routers\.material_audience_router import.*\n", "", text, flags=re.M)
text = re.sub(r"^router\.include_router\(material_audiences\).*\n", "", text, flags=re.M)
if text != orig:
    bak = init.with_suffix(".py.bak_audience502")
    if not bak.exists():
        bak.write_text(orig, encoding="utf-8")
    init.write_text(text, encoding="utf-8")
    print("patched routers/__init__.py")
else:
    print("routers/__init__.py unchanged")

router = root / "server/routers/material_audience_router.py"
if router.exists():
    bak = router.with_suffix(".py.broken_bak")
    if bak.exists():
        bak.unlink()
    router.rename(bak)
    print("quarantined material_audience_router.py")

shutil.copyfile(
    "/tmp/enterprise_gallery_audience_stub.py",
    str(root / "package/yuxi/services/enterprise_gallery_audience.py"),
)
print("installed stub enterprise_gallery_audience.py")

mls = root / "package/yuxi/services/material_library_service.py"
mt = mls.read_text(encoding="utf-8")
if "ensure_enterprise_gallery_audiences" in mt and "from yuxi.services.enterprise_gallery_audience import" not in mt:
    needle = "from yuxi.services.material_folder_permissions import ("
    idx = mt.find(needle)
    if idx < 0:
        raise SystemExit("cannot find insert point in MLS")
    end = mt.find(")\n", idx)
    if end < 0:
        raise SystemExit("cannot find end of permissions import")
    insert_at = end + 2
    import_line = (
        "from yuxi.services.enterprise_gallery_audience import (\n"
        "    ensure_enterprise_gallery_audiences,\n"
        ")\n"
    )
    mls.write_text(mt[:insert_at] + import_line + mt[insert_at:], encoding="utf-8")
    print("re-added MLS stub import")
else:
    print("MLS import state ok / already present")

print("verify init audience lines:")
for i, line in enumerate(init.read_text(encoding="utf-8").splitlines(), 1):
    if "audience" in line.lower():
        print(f"  {i}: {line}")
print("verify MLS import:")
for i, line in enumerate(mls.read_text(encoding="utf-8").splitlines(), 1):
    if "enterprise_gallery_audience" in line:
        print(f"  {i}: {line}")
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 180) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if err.strip() and err not in out:
        return (out + "\n" + err).strip()
    return out.strip()


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=20)
    try:
        print("=== audience bak head ===")
        print(
            run(
                c,
                f"sed -n '1,60p' {ROOT}/backend/package/yuxi/services/enterprise_gallery_audience.py.broken_bak",
            )
        )

        sftp = c.open_sftp()
        with sftp.file("/tmp/enterprise_gallery_audience_stub.py", "w") as f:
            f.write(STUB)
        with sftp.file("/tmp/_fix_audience_v2.py", "w") as f:
            f.write(HOST_SCRIPT)
        sftp.close()

        print("=== apply ===")
        print(run(c, "python3 /tmp/_fix_audience_v2.py"))

        print("=== restart api ===")
        print(run(c, f"cd {ROOT} && docker compose restart api"))

        for i in range(48):
            time.sleep(5)
            code = run(
                c,
                "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || true",
            ).strip()
            print(f"wait {i}: {code}")
            if code == "200":
                break
            if i in (3, 10, 20, 35):
                print(run(c, "docker logs api-dev --tail 25 2>&1"))

        print("=== final ===")
        print(
            run(
                c,
                "curl -s -o /dev/null -w 'api:%{http_code}\\n' http://127.0.0.1:5050/api/system/health; "
                "curl -s -o /dev/null -w '8080:%{http_code}\\n' http://127.0.0.1:8080/; "
                "docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' api-dev; "
                "docker logs api-dev --tail 35 2>&1",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
