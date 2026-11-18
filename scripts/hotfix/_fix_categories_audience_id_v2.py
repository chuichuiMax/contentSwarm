#!/usr/bin/env python3
"""Rewrite ensure_enterprise_gallery_audiences to no-op without audience_id column."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REWRITE_PY = r"""
from pathlib import Path
import re
import py_compile

path = Path("/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py")
text = path.read_text(encoding="utf-8")
bak = Path(str(path) + ".bak_categories_audience_v2")
if not bak.exists():
    bak.write_text(text, encoding="utf-8")

start = text.find("async def ensure_enterprise_gallery_audiences(")
if start < 0:
    raise SystemExit("function not found")
rest = text[start + 1 :]
m = re.search(r"\n(?:async )?def ", rest)
if not m:
    raise SystemExit("next function not found")
end = start + 1 + m.start()

new_fn = (
    "async def ensure_enterprise_gallery_audiences(db: AsyncSession) -> list[ContentMaterialAudience]:\n"
    "    \"\"\"Bind audiences to enterprise roots when Category.audience_id exists; else no-op.\"\"\"\n"
    "    audiences = await ensure_material_audiences(db)\n"
    "    if not hasattr(ContentMaterialCategory, \"audience_id\"):\n"
    "        return audiences\n"
    "    default_id = next(item.id for item in audiences if item.is_default)\n"
    "    woodwork = next((item for item in audiences if item.code == WOODWORK_AUDIENCE_CODE), None)\n"
    "    woodwork_id = woodwork.id if woodwork is not None else WOODWORK_AUDIENCE_ID\n"
    "    roots = list(\n"
    "        (\n"
    "            await db.execute(\n"
    "                select(ContentMaterialCategory).where(\n"
    "                    ContentMaterialCategory.material_type == \"image\",\n"
    "                    ContentMaterialCategory.visibility == \"enterprise\",\n"
    "                    ContentMaterialCategory.parent_id.is_(None),\n"
    "                    ContentMaterialCategory.deleted_at.is_(None),\n"
    "                )\n"
    "            )\n"
    "        ).scalars()\n"
    "    )\n"
    "    changed = False\n"
    "    for category in roots:\n"
    "        current = getattr(category, \"audience_id\", None)\n"
    "        if current:\n"
    "            continue\n"
    "        if category.name == WOODWORK_GALLERY_NAME:\n"
    "            category.audience_id = woodwork_id\n"
    "        else:\n"
    "            category.audience_id = default_id\n"
    "        changed = True\n"
    "    if changed:\n"
    "        await db.flush()\n"
    "    return audiences\n\n\n"
)
text = text[:start] + new_fn + text[end:]

out_lines = []
for line in text.splitlines(True):
    if re.search(r"\b(category|gallery|root)\.audience_id\s*=", line):
        out_lines.append(line)
        continue
    if "payload.audience_id" in line or "Field(" in line or 'changes["audience_id"]' in line:
        out_lines.append(line)
        continue
    line = re.sub(r"\b(category|gallery|root)\.audience_id\b", r"getattr(\1, 'audience_id', None)", line)
    out_lines.append(line)
text = "".join(out_lines)
path.write_text(text, encoding="utf-8")
py_compile.compile(str(path), doraise=True)
print("REWRITE_OK")
print("has_hasattr_guard", 'hasattr(ContentMaterialCategory, "audience_id")' in text)
left=[]
for i,l in enumerate(text.splitlines(),1):
    if re.search(r"\b(category|gallery|root)\.audience_id\b", l) and "getattr" not in l:
        left.append(f"{i}:{l}")
print("remaining", len(left))
for x in left[:40]:
    print(x)
"""

VERIFY = r'''
import os, urllib.request, urllib.error, asyncio, asyncpg, json, traceback
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
            key = "categories" if "categories" in body else "galleries"
            items = body.get(key) or body.get("items") or []
            print(json.dumps({"path": path, "status": resp.status, "count": len(items)}, ensure_ascii=False))
    except urllib.error.HTTPError as e:
        print("HTTP", path, e.code)
        print(e.read()[:500].decode("utf-8", "replace"))
    except Exception:
        traceback.print_exc()
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
        with sftp.file("/tmp/_rewrite_aud.py", "w") as f:
            f.write(REWRITE_PY)
        with sftp.file("/tmp/_verify_categories2.py", "w") as f:
            f.write(VERIFY)
        sftp.close()

        out = run(c, "python3 /tmp/_rewrite_aud.py", timeout=30)
        print(out)

        for i in range(20):
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
            "docker cp /tmp/_verify_categories2.py api-dev:/tmp/_verify_categories2.py && "
            "timeout 80 docker exec -w /app api-dev python /tmp/_verify_categories2.py",
            timeout=90,
        )
        logs = run(c, "docker logs api-dev --tail 60 2>&1 | tail -n 60", timeout=20)
        text = out + "\n===verify===\n" + verify + "\n===logs===\n" + logs
        OUT.write_text(text, encoding="utf-8")
        print(verify.encode("ascii", "replace").decode("ascii"))
        print(logs[-1500:].encode("ascii", "replace").decode("ascii"))
        return 0 if '"status": 200' in verify else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
