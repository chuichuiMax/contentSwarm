import os
import sys
import time
from pathlib import Path

import paramiko

PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD", "")
REPO = Path(__file__).resolve().parents[2]


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    return (stdout.read() + stderr.read()).decode(errors="replace")


def main() -> int:
    if not PASSWORD:
        print("DEPLOY_SSH_PASSWORD required", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("172.16.103.15", username="root", password=PASSWORD, timeout=30)
    sftp = c.open_sftp()
    for rel in [
        "backend/package/yuxi/services/ai_cover_prompt_catalog_service.py",
        "backend/server/routers/content_cover_router.py",
    ]:
        sftp.put(str(REPO / rel), f"/srv/contentswarm/{rel}")
    sftp.close()
    print(run(c, "docker exec api-dev ls /app/.venv/bin/pip 2>&1", timeout=30))
    print(
        run(
            c,
            "docker exec api-dev /app/.venv/bin/pip install -q alibabacloud-oss-v2 2>&1 | tail -5",
            timeout=300,
        )
    )
    print(run(c, "docker restart api-dev", timeout=60))
    time.sleep(45)
    print(
        run(
            c,
            "curl -s -o /tmp/p9.png -w 'code=%{http_code} size=%{size_download}' "
            "'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p9/preview.png' && "
            "file /tmp/p9.png",
        )
    )
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
