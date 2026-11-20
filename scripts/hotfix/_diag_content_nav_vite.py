#!/usr/bin/env python3
"""Check whether ContentStudioView and its first-level deps compile on 103.15 vite."""
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")

REMOTE_SCRIPT = r'''
import re
import urllib.request

def fetch(url, timeout=60):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()

print("=== vite ContentStudio ===")
code, body = fetch("http://127.0.0.1:5173/src/views/ContentStudioView.vue")
text = body.decode("utf-8", "replace")
print("code", code, "size", len(body))
print("has_err", ("Internal server error" in text) or ("Failed to resolve" in text) or ("ENOENT" in text))
print("head", text[:180].replace("\n", " "))

imps = set(re.findall(r'from "(/src/[^"]+)"', text))
imps |= set(re.findall(r'import\("(/src/[^"]+)"\)', text))
print("dep_count", len(imps))

bad = []
ok = 0
for path in sorted(imps):
    url = "http://127.0.0.1:5173" + path
    try:
        c2, b2 = fetch(url, timeout=45)
        head = b2[:160]
        if c2 != 200 or b"Internal server error" in head or b"Failed to resolve" in head or b"ENOENT" in head:
            bad.append((path, c2, head.decode("utf-8", "replace")))
        else:
            ok += 1
    except Exception as e:
        bad.append((path, "EX", str(e)[:200]))
print("ok", ok, "bad", len(bad))
for item in bad[:40]:
    print("BAD", item[0], item[1], repr(item[2])[:220])

# also probe a few critical modules explicitly
crit = [
    "/src/utils/contentRequestPayload.js",
    "/src/apis/base.js",
    "/src/stores/contentStudio.js",
    "/src/apis/content_api.js",
    "/src/components/content/ContentStudioToolbar.vue",
    "/src/components/material/CaseGalleryStyleFilter.vue",
]
print("=== critical modules ===")
for path in crit:
    try:
        c2, b2 = fetch("http://127.0.0.1:5173" + path, timeout=45)
        head = b2[:120].decode("utf-8", "replace")
        print(path, c2, len(b2), "err" if ("Internal server error" in head or "Failed to resolve" in head) else "ok")
    except Exception as e:
        print(path, "EX", e)
'''


def run(c, cmd, timeout=180):
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
        with sftp.file("/tmp/_diag_content_nav_vite_remote.py", "w") as f:
            f.write(REMOTE_SCRIPT)
        sftp.close()

        parts = []
        parts.append("=== containers ===")
        parts.append(run(c, "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'web-dev|api-dev' || true"))
        parts.append("=== web recent errors ===")
        parts.append(
            run(
                c,
                "docker logs web-dev --since 45m 2>&1 | grep -iE 'error|ENOENT|Failed|ContentStudio|Internal|500' | tail -n 50 || true",
            )
        )
        parts.append("=== node syntax checks ===")
        parts.append(
            run(
                c,
                "docker exec -w /app web-dev sh -lc '"
                "node --check src/utils/contentRequestPayload.js; "
                "node --check src/apis/base.js; "
                "node --check src/stores/contentStudio.js; "
                "echo SYNTAX_OK'",
            )
        )
        parts.append("=== vite dep probe ===")
        parts.append(run(c, "python3 /tmp/_diag_content_nav_vite_remote.py", timeout=240))
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:9000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
