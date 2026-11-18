#!/usr/bin/env python3
"""Fix ContentMaterialCategory.to_dict audience_id AttributeError."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

PATCH = r"""
from pathlib import Path
import re
import py_compile

path = Path("/srv/contentswarm/backend/package/yuxi/storage/postgres/models_content.py")
text = path.read_text(encoding="utf-8")
bak = Path(str(path) + ".bak_audience_to_dict")
if not bak.exists():
    bak.write_text(text, encoding="utf-8")

# Replace self.audience_id reads in to_dict with getattr
old = '"audience_id": self.audience_id,'
new = '"audience_id": getattr(self, "audience_id", None),'
count = text.count(old)
if count == 0 and 'getattr(self, "audience_id", None)' not in text:
    # broader: any self.audience_id in this file
    text2, n = re.subn(r"\bself\.audience_id\b", 'getattr(self, "audience_id", None)', text)
    text = text2
    print("replaced_self_audience", n)
else:
    text = text.replace(old, new)
    print("replaced_to_dict_lines", count)

# If model somehow declares audience_id in to_dict but not as Column, ensure getattr path exists
path.write_text(text, encoding="utf-8")
py_compile.compile(str(path), doraise=True)
print("SYNTAX_OK")
print("has_getattr", 'getattr(self, "audience_id", None)' in text)
print("raw_self", "self.audience_id" in text)
"""

VERIFY = r'''
import os, urllib.request, urllib.error, asyncio, asyncpg, json
from yuxi.utils.auth_utils import AuthUtils

async def admin_uid():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("select id, username, role from users order by id asc limit 20")
        for r in rows:
            role = str(r["role"] or "").lower()
            name = str(r["username"] or "").lower()
            if role in {"admin", "superadmin"} or name in {"admin", "superadmin"}:
                return r["id"]
        return rows[0]["id"]
    finally:
        await conn.close()

uid = asyncio.run(admin_uid())
token = AuthUtils.create_access_token({"sub": str(uid)})
for path in [
    "/api/material-library/categories?material_type=image",
    "/api/material-library/galleries?industry_slug=decoration",
]:
    url = "http://127.0.0.1:5050" + path
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            items = body.get("categories") or body.get("galleries") or body.get("items") or []
            print(json.dumps({"path": path, "status": resp.status, "count": len(items)}, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        print("HTTP", path, e.code)
        print(e.read()[:500].decode("utf-8", "replace"))
'''


def run(c, cmd, timeout=90):
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
        chan.close()
        raise TimeoutError(cmd[:120])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        sftp = c.open_sftp()
        with sftp.file("/tmp/_fix_todict_aud.py", "w") as f:
            f.write(PATCH)
        with sftp.file("/tmp/_verify_cat3.py", "w") as f:
            f.write(VERIFY)
        sftp.close()
        out = run(c, "python3 /tmp/_fix_todict_aud.py", timeout=30)
        print(out)
        for i in range(25):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' "
                "http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print("health", i, code)
            if code.endswith("200"):
                break
            time.sleep(2)
        verify = run(
            c,
            "docker cp /tmp/_verify_cat3.py api-dev:/tmp/_verify_cat3.py && "
            "timeout 80 docker exec -w /app api-dev python /tmp/_verify_cat3.py",
            timeout=90,
        )
        logs = run(c, "docker logs api-dev --tail 40 2>&1 | tail -n 40", timeout=20)
        text = out + "\n===verify===\n" + verify + "\n===logs===\n" + logs
        OUT.write_text(text, encoding="utf-8")
        print(verify.encode("ascii", "replace").decode("ascii"))
        if '"status": 200' not in verify:
            print(logs[-1200:].encode("ascii", "replace").decode("ascii"))
            return 1
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
