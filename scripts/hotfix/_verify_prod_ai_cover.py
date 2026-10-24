from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.17"


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    time.sleep(5)
    cmds = [
        "curl -s -o /dev/null -w 'health %{http_code}\\n' http://127.0.0.1:5050/api/system/health",
        "curl -sL -o /dev/null -w 'p1 %{http_code} %{size_download}\\n' "
        "'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p1/preview.png?content_type_code=CT06'",
        "curl -sL -o /dev/null -w 'p17 %{http_code} %{size_download}\\n' "
        "'http://127.0.0.1:5050/api/content/covers/ai-cover-prompts/p17/preview.png?content_type_code=CT06'",
        "docker exec api-dev python -c \"import os; print('subdir', os.getenv('AI_COVER_OSS_SUBDIR'))\"",
    ]
    for cmd in cmds:
        _, o, e = c.exec_command(cmd, timeout=120)
        print(o.read().decode("utf-8", errors="replace").strip())
        err = e.read().decode().strip()
        if err:
            print("err:", err)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
