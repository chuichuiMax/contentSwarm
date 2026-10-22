import os
import sys
import time
from pathlib import Path

import paramiko

REPO = Path(__file__).resolve().parents[2]
FILES = [
    "backend/package/yuxi/content_cover/ai_cover_prompt_catalog.py",
    "backend/package/yuxi/services/ai_cover_prompt_catalog_service.py",
    "backend/server/routers/content_cover_router.py",
]


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.16.103.15", username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in FILES:
        sftp.put(str(REPO / rel), f"/srv/contentswarm/{rel}")
    sftp.close()
    c.exec_command("docker restart api-dev")
    time.sleep(45)
    _, o, _ = c.exec_command(
        "curl -s -o /tmp/p9.png -w '%{http_code} %{size_download}' "
        "http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p9/preview.png",
        timeout=60,
    )
    print(o.read().decode())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
