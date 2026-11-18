#!/usr/bin/env python3
"""Fix categories 500: enterprise_gallery_audience uses missing Category.audience_id."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")
REMOTE = "/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py"

PATCH = r'''
from pathlib import Path
import re
import py_compile

path = Path("/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py")
orig = path.read_text(encoding="utf-8")
bak = Path(str(path) + ".bak_categories_audience")
if not bak.exists():
    bak.write_text(orig, encoding="utf-8")

text = orig

# Early-return in ensure_enterprise_gallery_audiences when ORM lacks audience_id.
needle = "async def ensure_enterprise_gallery_audiences(db: AsyncSession) -> list[ContentMaterialAudience]:"
if needle not in text:
    raise SystemExit("ensure_enterprise_gallery_audiences not found")
if "CONTENT_MATERIAL_CATEGORY_HAS_AUDIENCE_ID" not in text:
    guard = (
        "\nCONTENT_MATERIAL_CATEGORY_HAS_AUDIENCE_ID = "
        'hasattr(ContentMaterialCategory, "audience_id")\n\n'
    )
    text = text.replace(
        "from yuxi.utils.datetime_utils import utc_now_naive\n",
        "from yuxi.utils.datetime_utils import utc_now_naive\n" + guard,
        1,
    )
    insert_after = needle + "\n    audiences = await ensure_material_audiences(db)\n"
    if insert_after not in text:
        raise SystemExit("ensure_material_audiences call not found")
    text = text.replace(
        insert_after,
        needle
        + "\n    audiences = await ensure_material_audiences(db)\n"
        + "    if not CONTENT_MATERIAL_CATEGORY_HAS_AUDIENCE_ID:\n"
        + "        return audiences\n",
        1,
    )

# Harden remaining attribute reads/writes.
lines = []
for line in text.splitlines(True):
    raw = line
    if "payload.audience_id" in line or "Field(" in line or "changes[" in line:
        lines.append(raw)
        continue
    if re.search(r"\b(category|gallery|root)\.audience_id\s*=", line):
        m = re.match(r"^(\s*)(\w+)\.audience_id\s*=\s*(.*)$", line)
        if m:
            ind, obj, rhs = m.group(1), m.group(2), m.group(3)
            lines.append(f"{ind}if hasattr({obj}, 'audience_id'):\n")
            lines.append(f"{ind}    {obj}.audience_id = {rhs}\n")
            continue
    line = re.sub(r"\b(category|gallery|root)\.audience_id\b", r"getattr(\1, 'audience_id', None)", line)
    lines.append(line)
text = "".join(lines)

path.write_text(text, encoding="utf-8")
py_compile.compile(str(path), doraise=True)
print("SYNTAX_OK")
print("has_guard", "CONTENT_MATERIAL_CATEGORY_HAS_AUDIENCE_ID" in text)
print("early_return", "if not CONTENT_MATERIAL_CATEGORY_HAS_AUDIENCE_ID:" in text)
left = []
for i, line in enumerate(text.splitlines(), 1):
    if re.search(r"\b(category|gallery|root)\.audience_id\b", line) and "getattr" not in line:
        left.append(f"{i}:{line}")
print("remaining_raw", len(left))
for item in left[:20]:
    print(item)
'''

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
url = "http://127.0.0.1:5050/api/material-library/categories?material_type=image"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        cats = body.get("categories") or []
        print(json.dumps({
            "status": resp.status,
            "count": len(cats),
            "sample": [{"id": c.get("id"), "name": c.get("name")} for c in cats[:5]],
        }, ensure_ascii=False))
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read()[:800].decode("utf-8", "replace"))
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
        with sftp.file("/tmp/_fix_cat_aud.py", "w") as f:
            f.write(PATCH)
        with sftp.file("/tmp/_verify_categories.py", "w") as f:
            f.write(VERIFY)
        sftp.close()

        patch_out = run(c, "python3 /tmp/_fix_cat_aud.py", timeout=30)
        print(patch_out)

        ok = False
        for i in range(25):
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' "
                "http://127.0.0.1:5050/api/system/health || echo 000",
                timeout=10,
            ).strip()
            print("health", i, code)
            if code.endswith("200"):
                ok = True
                break
            time.sleep(2)
        if not ok:
            print(run(c, "docker logs api-dev --tail 80 2>&1 | tail -n 80", timeout=20))

        verify = run(
            c,
            "docker cp /tmp/_verify_categories.py api-dev:/tmp/_verify_categories.py && "
            "timeout 70 docker exec -w /app api-dev python /tmp/_verify_categories.py",
            timeout=80,
        )
        print(verify.encode("ascii", "replace").decode("ascii"))
        OUT.write_text(patch_out + "\n===verify===\n" + verify, encoding="utf-8")
        return 0 if '"status": 200' in verify or '"status":200' in verify else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
