#!/usr/bin/env python3
"""Deploy contentTestCases JSON fix to 103.15 and verify ContentStudio loads."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
ROOT = Path(__file__).resolve().parents[2]
LOCAL_FILES = [
    ROOT / "web/src/data/contentTestCases.js",
    ROOT / "web/src/data/_content_test_case_bodies.json",
]
REMOTE_DIR = "/srv/contentswarm/web/src/data"
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
        sftp = c.open_sftp()
        for path in LOCAL_FILES:
            remote = f"{REMOTE_DIR}/{path.name}"
            sftp.put(str(path), remote)
            # also into running web-dev bind mount if different
            try:
                sftp.put(str(path), f"/tmp/{path.name}")
            except Exception:
                pass
        sftp.close()

        parts = []
        parts.append("=== sync into web-dev container ===")
        parts.append(
            run(
                c,
                "docker cp /srv/contentswarm/web/src/data/contentTestCases.js web-dev:/app/src/data/contentTestCases.js && "
                "docker cp /srv/contentswarm/web/src/data/_content_test_case_bodies.json web-dev:/app/src/data/_content_test_case_bodies.json && "
                "ls -la /srv/contentswarm/web/src/data/contentTestCases.js /srv/contentswarm/web/src/data/_content_test_case_bodies.json && "
                "docker exec web-dev ls -la /app/src/data/contentTestCases.js /app/src/data/_content_test_case_bodies.json",
            )
        )
        parts.append("=== vite transform checks ===")
        parts.append(
            run(
                c,
                "curl -s -m 30 -o /tmp/ctc.js -w 'contentTestCases:%{http_code} %{size_download}\\n' "
                "http://127.0.0.1:5173/src/data/contentTestCases.js; "
                "curl -s -m 30 -o /tmp/bodies.json -w 'bodies:%{http_code} %{size_download}\\n' "
                "http://127.0.0.1:5173/src/data/_content_test_case_bodies.json; "
                "curl -s -m 60 -o /tmp/cs.vue.js -w 'ContentStudio:%{http_code} %{size_download}\\n' "
                "http://127.0.0.1:5173/src/views/ContentStudioView.vue; "
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "t=Path('/tmp/ctc.js').read_text(encoding='utf-8',errors='replace')\n"
                "print('has_raw', '?raw' in t)\n"
                "print('has_json', '_content_test_case_bodies.json' in t or 'content_test_case_bodies' in t)\n"
                "print('has_err', 'Internal server error' in t or 'Failed to resolve' in t)\n"
                "print('head', t[:180].replace('\\n',' '))\n"
                "PY",
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:8000])
        return 0 if "ContentStudio:200" in text and "has_raw False" in text else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
