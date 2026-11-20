#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=120):
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
        # Fetch transformed ContentStudio and search for import failures / missing modules
        parts = []
        parts.append(
            run(
                c,
                "curl -s -m 30 'http://127.0.0.1:5173/src/views/ContentStudioView.vue' "
                "| python3 -c \"import sys;t=sys.stdin.read();print('len',len(t));"
                "print('has_err', 'Internal Server Error' in t or 'Failed to resolve' in t);"
                "print('has_payload', 'contentRequestPayload' in t);"
                "print('has_case_gallery', 'CaseGalleryStyleFilter' in t)\"",
            )
        )
        # Check content bootstrap API used by store
        parts.append("=== contentStudio loadBootstrap code ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "txt=Path('/srv/contentswarm/web/src/stores/contentStudio.js').read_text(encoding='utf-8')\n"
                "i=txt.find('async function loadBootstrap')\n"
                "print(txt[i:i+900])\n"
                "PY"
            )
        )
        parts.append("=== content_api bootstrap methods ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "txt=Path('/srv/contentswarm/web/src/apis/content_api.js').read_text(encoding='utf-8')\n"
                "for line in txt.splitlines():\n"
                "  if 'bootstrap' in line.lower() or 'industry' in line.lower() and 'template' in line.lower():\n"
                "    print(line)\n"
                "PY"
            )
        )
        # Try authenticated request simulation: login + hit content page APIs
        parts.append("=== login and hit content bootstrap ===")
        parts.append(
            run(
                c,
                "docker cp /tmp/_noop 2>/dev/null; "
                "python3 - <<'PY'\n"
                "import os,json,urllib.request,urllib.error,asyncio,asyncpg\n"
                "async def token():\n"
                "  url=(os.environ.get('POSTGRES_URL') or '').replace('postgresql+asyncpg://','postgresql://')\n"
                "  # run inside container instead\n"
                "  print('hostside skip')\n"
                "asyncio.run(token())\n"
                "PY"
            )
        )
        # Run inside api container to mint token and call content bootstrap via api
        verify = r'''
import os, json, urllib.request, urllib.error, asyncio, asyncpg
from yuxi.utils.auth_utils import AuthUtils

async def admin_uid():
    url = (os.environ.get("POSTGRES_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("select id, username, role from users order by id asc limit 20")
        for r in rows:
            if str(r["role"]).lower() in {"admin","superadmin"} or str(r["username"]).lower() in {"admin","superadmin"}:
                return r["id"]
        return rows[0]["id"]
    finally:
        await conn.close()

uid = asyncio.run(admin_uid())
token = AuthUtils.create_access_token({"sub": str(uid)})
headers = {"Authorization": f"Bearer {token}"}
for path in [
    "/api/content/bootstrap",
    "/api/content/studio/bootstrap",
    "/api/content/industry-templates",
    "/api/content/tasks",
]:
    req = urllib.request.Request("http://127.0.0.1:5050"+path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()[:200]
            print(path, resp.status, body[:120])
    except urllib.error.HTTPError as e:
        print(path, "HTTP", e.code, e.read()[:200])
    except Exception as e:
        print(path, "ERR", e)
'''
        sftp = c.open_sftp()
        with sftp.file("/tmp/_content_boot.py", "w") as f:
            f.write(verify)
        sftp.close()
        parts.append(
            run(
                c,
                "docker cp /tmp/_content_boot.py api-dev:/tmp/_content_boot.py && "
                "timeout 40 docker exec -w /app api-dev python /tmp/_content_boot.py",
                timeout=55,
            )
        )
        # Check if web container can resolve ContentStudio dependencies that often break
        parts.append("=== check lucide FilePenLine in AppLayout ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "txt=Path('/srv/contentswarm/web/src/layouts/AppLayout.vue').read_text(encoding='utf-8')\n"
                "print('FilePenLine import', 'FilePenLine' in txt)\n"
                "print('import block:')\n"
                "for line in txt.splitlines()[:80]:\n"
                "  if 'lucide' in line or 'FilePen' in line or 'import' in line and 'from' in line:\n"
                "    print(line)\n"
                "PY"
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:6500])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
