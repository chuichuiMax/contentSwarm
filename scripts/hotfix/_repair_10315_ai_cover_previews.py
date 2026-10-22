"""Fix 103.15 AI cover previews: UTF-8 OSS subdir, OSS SDK, restart api (no recreate)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
REPO = Path(__file__).resolve().parents[2]

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)

# Sync latest backend/frontend + compose
sync_files = [
    "docker-compose.yml",
    "backend/package/yuxi/content_cover/ai_cover_prompt_catalog.py",
    "backend/package/yuxi/content_cover/ai_cover_prompt.py",
    "backend/package/yuxi/services/ai_cover_prompt_catalog_service.py",
    "backend/server/routers/content_cover_router.py",
    "web/src/views/ContentStudioView.vue",
    "web/src/apis/content_api.js",
]
sftp = c.open_sftp()
for rel in sync_files:
    src = REPO / rel
    if src.is_file():
        sftp.put(str(src), f"{BASE}/{rel}")
        print("OK", rel)
sftp.close()

# Fix AI_COVER_OSS_SUBDIR in .env (must be UTF-8  AI封面)
fix_env = """python3 - <<'PY'
import re
from pathlib import Path
p = Path("/srv/contentswarm/.env")
text = p.read_text(encoding="utf-8", errors="replace")
sub = "AI封面"
if "AI_COVER_OSS_SUBDIR=" in text:
    text = re.sub(r"^AI_COVER_OSS_SUBDIR=.*$", f"AI_COVER_OSS_SUBDIR={sub}", text, flags=re.M)
else:
    text = text.rstrip() + f"\\nAI_COVER_OSS_SUBDIR={sub}\\n"
p.write_text(text, encoding="utf-8")
print("AI_COVER_OSS_SUBDIR=", sub)
PY"""

_, o, _ = c.exec_command(fix_env, timeout=60)
print(o.read().decode())

# Recreate api only with new compose command (installs OSS SDK on start); web restart
compose = f"cd {BASE} && docker compose --env-file .env up -d --no-deps api web"
_, o, e = c.exec_command(compose, timeout=300)
print(o.read().decode())
err = e.read().decode().strip()
if err:
    print(err)

print("waiting for api healthy...")
import time

for i in range(36):
    _, o, _ = c.exec_command(
        "curl -sf http://127.0.0.1:5050/api/system/health >/dev/null && echo ok || echo wait",
        timeout=15,
    )
    if o.read().decode().strip() == "ok":
        print(f"healthy ~{i * 5}s")
        break
    time.sleep(5)

checks = [
    "docker exec api-dev python -c 'import alibabacloud_oss_v2; print(\"oss_v2 ok\")'",
    "docker exec api-dev python -c \"import os; print('subdir', os.getenv('AI_COVER_OSS_SUBDIR'))\"",
    "docker exec api-dev python -c \"import asyncio; from yuxi.services.ai_cover_prompt_catalog_service import list_ai_cover_prompt_catalog; r=asyncio.run(list_ai_cover_prompt_catalog(content_type_code='CT06')); print('count', len(r['prompts']))\"",
    "curl -s -o /dev/null -w 'p1 %{http_code} %{size_download}\\n' 'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p1/preview.png?content_type_code=CT06'",
    "curl -s -o /dev/null -w 'p7 %{http_code} %{size_download}\\n' 'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p7/preview.png?content_type_code=CT06'",
]
for cmd in checks:
    _, o, e = c.exec_command(cmd, timeout=180)
    out = o.read().decode().strip()
    er = e.read().decode().strip()
    print(out or er)

c.close()
