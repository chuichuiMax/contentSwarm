"""Fix test server HyCanvas: expose via :8080 (public 8005 blocked)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
NGINX_CONF = "/etc/nginx/conf.d/contentswarm-dev-proxy.conf"
PUBLIC = "http://124.232.148.28:8080"

NGINX_BODY = r"""# 公网 :8080 → web-dev (Vite 5173)；HyCanvas 走同端口避免 8005/3000 未放行。
server {
    listen 8080;
    listen [::]:8080;
    server_name _;

    client_max_body_size 30M;

    location ^~ /api/v1/ {
        proxy_pass http://127.0.0.1:8005;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location ^~ /locales/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location ^~ /_next/ {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location ^~ /dashboard {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location ^~ /editor {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location / {
        proxy_pass http://127.0.0.1:5173;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
"""


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    repo = Path(__file__).resolve().parents[2]
    for p in (repo / ".deploy" / "10315.password",):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def _patch_env(text: str) -> str:
    updates = {
        "HYCANVAS_PUBLIC_URL": PUBLIC,
        "HYCANVAS_DEV_PUBLIC_URL": PUBLIC,
        "CONTENTSWARM_PUBLIC_URL": PUBLIC,
    }
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        matched = False
        for key, value in updates.items():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                seen.add(key)
                matched = True
                break
        if not matched:
            lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    with sftp.open(f"{BASE}/docker/nginx/contentswarm-host-dev-proxy.conf", "w") as f:
        f.write(NGINX_BODY.encode("utf-8"))
    with sftp.open(f"{BASE}/.env", "r") as f:
        env_text = f.read().decode("utf-8", errors="replace")
    with sftp.open(f"{BASE}/.env", "w") as f:
        f.write(_patch_env(env_text).encode("utf-8"))
    with sftp.file(NGINX_CONF, "w") as f:
        f.write(NGINX_BODY.encode("utf-8"))
    sftp.close()
    for cmd in (
        "nginx -t",
        "systemctl reload nginx",
        f"cd {BASE} && docker compose --env-file .env up -d --no-deps api web hycanvas-app",
    ):
        _, o, e = c.exec_command(cmd, timeout=180)
        out = o.read().decode()
        err = e.read().decode()
        print(f">>> {cmd}")
        if out.strip():
            print(out.strip())
        if err.strip():
            print(err.strip(), file=sys.stderr)
    time.sleep(12)
    checks = [
        f"curl -s -o /dev/null -w '8080 dashboard %{{http_code}}\\n' --max-time 15 {PUBLIC.replace('124.232.148.28', '127.0.0.1')}/dashboard/",
        "curl -s -o /dev/null -w '8080 api/v1 healthz via proxy %{http_code}\\n' --max-time 10 http://127.0.0.1:8080/api/v1/ 2>/dev/null || "
        "curl -s -o /dev/null -w '8080 api/v1 %{http_code}\\n' --max-time 10 -X POST http://127.0.0.1:8080/api/v1/auth/integration 2>/dev/null || true",
    ]
    for cmd in checks:
        _, o, _ = c.exec_command(cmd, timeout=30)
        print(o.read().decode().strip())
    c.close()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
