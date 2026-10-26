from __future__ import annotations

import os
import sys

import paramiko

HOST = "172.16.103.15"


def main() -> int:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    # Old full-site proxy without hycanvas paths — disable to avoid duplicate listen 8080.
    c.exec_command(
        "if grep -q 'listen 8080' /etc/nginx/conf.d/contentswarm.conf 2>/dev/null; then "
        "mv /etc/nginx/conf.d/contentswarm.conf /etc/nginx/conf.d/contentswarm.conf.disabled-8080; fi",
        timeout=30,
    )
    _, o, e = c.exec_command("nginx -t 2>&1 && systemctl reload nginx 2>&1", timeout=30)
    print(o.read().decode())
    print(e.read().decode())
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
