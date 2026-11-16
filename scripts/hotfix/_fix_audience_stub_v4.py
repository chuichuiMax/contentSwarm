#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    out = []
    err = []
    while True:
        if chan.recv_ready():
            out.append(chan.recv(65535))
        if chan.recv_stderr_ready():
            err.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready():
                out.append(chan.recv(65535))
            while chan.recv_stderr_ready():
                err.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    text = (b"".join(out) + b"".join(err)).decode("utf-8", "replace")
    return text.encode("ascii", "replace").decode("ascii")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        print("=== stub file exists? ===")
        print(
            run(
                c,
                "ls -la /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py; "
                "wc -l /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py; "
                "python3 -c 'import ast; ast.parse(open(\"/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py\",encoding=\"utf-8\").read()); print(\"stub_syntax_ok\")'",
            )
        )
        print("=== import check in container ===")
        print(
            run(
                c,
                "docker exec api-dev python -c \"from yuxi.services.enterprise_gallery_audience import reject_enterprise_audience_denied, ensure_enterprise_gallery_audiences, audience_catalog, filter_categories_by_audience, validate_audience_id; print('import_ok')\" 2>&1 || true",
            )
        )
        print("=== api state / logs ===")
        print(run(c, "docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' api-dev"))
        print(run(c, "docker logs api-dev --tail 60 2>&1 | tail -n 60"))

        print("=== restart api ===")
        print(run(c, "cd /srv/contentswarm && docker compose restart api", timeout=120))
        for i in range(40):
            time.sleep(3)
            code = run(
                c,
                "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/api/system/health || echo 000",
            ).strip()
            print(f"wait {i}: {code}")
            if code.endswith("200"):
                break
            if i in (8, 20):
                print(run(c, "docker logs api-dev --tail 40 2>&1 | tail -n 40"))

        # get admin password from postgres
        print("=== admin user from db ===")
        print(
            run(
                c,
                "cd /srv/contentswarm && docker compose exec -T postgres psql -U yuxi -d yuxi -c \"select uid,username,role from users order by created_at nulls last limit 20;\" 2>&1 | head -40 || "
                "docker exec $(docker ps --format '{{.Names}}' | grep -E 'postgres|db' | head -1) "
                "psql -U yuxi -d yuxi -c 'select uid,username from users limit 10;' 2>&1 | head -40",
            )
        )

        # Try login with password from compose env
        print("=== compose env admin ===")
        print(
            run(
                c,
                "cd /srv/contentswarm && (grep -E 'ADMIN|PASSWORD|SUPER' .env docker-compose.yml 2>/dev/null | sed 's/PASS.*/PASS=***/' | head -40); "
                "docker inspect api-dev --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -iE 'ADMIN|PASS|SUPER' | sed 's/=.*/=***/' | head -40",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
