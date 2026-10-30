"""Fix 8080: proxy /api to api-dev; recreate web-dev as Vite dev if it is nginx static."""
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
    p = repo / ".deploy" / "10315.password"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def main() -> int:
    pw = _pw()
    if not pw:
        print("Set DEPLOY_SSH_PASSWORD", file=sys.stderr)
        return 1
    repo = Path(__file__).resolve().parents[2]
    nginx = (repo / "docker/nginx/contentswarm-host-dev-proxy.conf").read_text(encoding="utf-8")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=pw, timeout=30)
    sftp = c.open_sftp()
    sftp.put(str(repo / "docker/nginx/contentswarm-host-dev-proxy.conf"), f"{BASE}/docker/nginx/contentswarm-host-dev-proxy.conf")
    with sftp.file(NGINX_CONF, "w") as f:
        f.write(nginx.encode("utf-8"))
    sftp.close()
    cmds = [
        "nginx -t && systemctl reload nginx",
        f"cd {BASE} && docker compose --env-file .env build web --target development",
        f"cd {BASE} && docker compose --env-file .env up -d --force-recreate --no-deps web",
        "docker exec web-dev ps aux | head -3",
        "curl -s http://127.0.0.1:8080/api/system/health",
    ]
    for cmd in cmds:
        print(">>>", cmd[:90])
        _, o, e = c.exec_command(cmd, timeout=600)
        out = o.read().decode("utf-8", errors="replace").strip()
        err = e.read().decode("utf-8", errors="replace").strip()
        if out:
            print(out[:800])
        if err and "Built" not in err:
            print(err[:400], file=sys.stderr)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
