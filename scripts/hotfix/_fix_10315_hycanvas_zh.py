"""Apply HyCanvas Chinese UI fix on 103.15: nginx /locales, DB locale, sync web."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
BASE = "/srv/contentswarm"
NGINX_CONF = "/etc/nginx/conf.d/contentswarm-dev-proxy.conf"


def _pw() -> str:
    direct = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if direct:
        return direct
    repo = Path(__file__).resolve().parents[2]
    for p in (repo / ".deploy" / "10315.password",):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    repo = Path(__file__).resolve().parents[2]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    for rel in (
        "docker/nginx/contentswarm-host-dev-proxy.conf",
        "web/vite.config.js",
        "web/src/views/HyCanvasWorkspaceView.vue",
        "docker/hycanvas/init-integration.sh",
    ):
        sftp.put(str(repo / rel), f"{BASE}/{rel}")
        print(f"OK {rel}")
    nginx_body = (repo / "docker/nginx/contentswarm-host-dev-proxy.conf").read_text(encoding="utf-8")
    with sftp.file(NGINX_CONF, "w") as f:
        f.write(nginx_body.encode("utf-8"))
    sftp.close()
    cmds = [
        "nginx -t && systemctl reload nginx",
        f"cd {BASE} && docker compose --env-file .env up -d --no-deps web",
        (
            "docker exec contentswarm-hycanvas-db-1 psql "
            "'postgresql://hycanvas:hycanvas-local-password@localhost:5432/hycanvas' "
            "-c \"UPDATE users SET locale='zh-CN' "
            "WHERE id='00000000-0000-4000-8000-000000001001';\""
        ),
        "curl -s -o /dev/null -w 'locales %{http_code} bytes=%{size_download}\\n' "
        "http://127.0.0.1:8080/locales/zh-cn.json",
    ]
    for cmd in cmds:
        _, o, e = c.exec_command(cmd, timeout=180)
        out = o.read().decode().strip()
        err = e.read().decode().strip()
        print(">>>", cmd[:70])
        if out:
            print(out)
        if err:
            print(err, file=sys.stderr)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
