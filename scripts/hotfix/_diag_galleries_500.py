#!/usr/bin/env python3
"""Capture galleries 500 traceback on 172.16.103.15 and patch missing audience symbols."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 90) -> str:
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks = []
    while True:
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
    return b"".join(chunks).decode("utf-8", "replace").encode("ascii", "replace").decode("ascii")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        print("=== recent galleries errors ===")
        print(
            run(
                c,
                "docker logs api-dev --since 30m 2>&1 | grep -E 'galleries|NameError|AttributeError|Traceback|ERROR' | tail -80",
            )
        )
        print("=== list_galleries / audience refs in MLS ===")
        print(
            run(
                c,
                "grep -nE 'def list_.*galler|galleries|audience_catalog|filter_categories_by_audience|"
                "ensure_enterprise_gallery|resolve_user_material|enterprise_category_visible|"
                "match_audience|audience_id' "
                "/srv/contentswarm/backend/package/yuxi/services/material_library_service.py | head -80",
            )
        )
        print("=== current stub ===")
        print(run(c, "sed -n '1,120p' /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py"))
        print("=== bak funcs ===")
        print(
            run(
                c,
                "grep -nE '^(async )?def |^class ' "
                "/srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py.broken_bak",
            )
        )
        print("=== router galleries ===")
        print(
            run(
                c,
                "grep -n galleries /srv/contentswarm/backend/server/routers/material_library_router.py | head -20",
            )
        )
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
