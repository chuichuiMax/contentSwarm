#!/usr/bin/env python3
"""Hotfix galleries 500 caused by missing ContentMaterialCategory.audience_id."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
MLS = "/srv/contentswarm/backend/package/yuxi/services/material_library_service.py"

PATCH = r'''
from pathlib import Path
import re
import shutil

mls = Path("/srv/contentswarm/backend/package/yuxi/services/material_library_service.py")
orig = mls.read_text(encoding="utf-8")
bak = Path(str(mls) + ".bak_audience_id")
if not bak.exists():
    bak.write_text(orig, encoding="utf-8")

out = []
for line in orig.splitlines(True):
    stripped = line.lstrip()
    # Drop constructor kwarg lines that set audience_id on ContentMaterialCategory
    if re.match(r"audience_id\s*=\s*audience_id,?\s*$", stripped):
        continue
    # Guard assignments
    m = re.match(r"^(\s*)(category|gallery)\.audience_id\s*=\s*(.*)$", line)
    if m:
        ind, obj, rhs = m.group(1), m.group(2), m.group(3)
        out.append(f"{ind}if hasattr({obj}, 'audience_id'):\n")
        out.append(f"{ind}    {obj}.audience_id = {rhs}\n")
        continue
    # Safe reads (skip payload/changes)
    if "payload.audience_id" not in line and 'changes["audience_id"]' not in line and "Field(" not in line:
        line = re.sub(r"\bcategory\.audience_id\b", "getattr(category, 'audience_id', None)", line)
        line = re.sub(r"\bgallery\.audience_id\b", "getattr(gallery, 'audience_id', None)", line)
        # also inline kwargs in multi-arg calls
        line = re.sub(r",\s*audience_id=audience_id\b", "", line)
        line = re.sub(r"\baudience_id=audience_id,\s*", "", line)
    out.append(line)

text = "".join(out)
mls.write_text(text, encoding="utf-8")
print("patched bytes", len(text))
# verify
import py_compile
py_compile.compile(str(mls), doraise=True)
print("SYNTAX_OK")
left = []
for i, line in enumerate(text.splitlines(), 1):
    if re.search(r"\b(category|gallery)\.audience_id\b", line):
        left.append(f"{i}:{line}")
print("remaining_raw", len(left))
for x in left[:20]:
    print(x)
'''


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
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
        try:
            chan.close()
        except Exception:
            pass
        raise TimeoutError(cmd[:80])
    return b"".join(chunks).decode("utf-8", "replace").encode("ascii", "replace").decode("ascii")


def main() -> int:
    print("connecting...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=15, banner_timeout=15, auth_timeout=15)
    print("connected")
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_fix_aud_id2.py", "w") as f:
            f.write(PATCH)
        sftp.close()

        print("=== patch ===")
        print(run(c, "python3 /tmp/_fix_aud_id2.py", timeout=30))

        print("=== health ===")
        ok = False
        for i in range(25):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print(f"wait {i}: {code}")
            if code.endswith("200"):
                ok = True
                break
            time.sleep(2)
        if not ok:
            print(run(c, "docker logs api-dev --tail 50 2>&1 | tail -n 50", timeout=20))

        # Lightweight runtime check: evaluate the getattr expression path by compiling list_image_galleries
        print("=== import MLS in container ===")
        print(
            run(
                c,
                "docker exec api-dev python -c \"from yuxi.services import material_library_service as m; "
                "import inspect; src=inspect.getsource(m.list_image_galleries); "
                "print('getattr_ok', \\\"getattr(category, 'audience_id', None)\\\" in src); "
                "print('raw_attr', 'category.audience_id' in src)\"",
                timeout=60,
            )
        )

        print("=== done ===")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
