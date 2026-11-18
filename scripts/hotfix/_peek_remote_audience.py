#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = os.path.splitext(__file__)[0] + ".out.txt"


def run(c, cmd, timeout=40):
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
        parts = []
        parts.append("=== enterprise_gallery_audience.py ===")
        parts.append(run(c, "wc -l /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py"))
        parts.append(run(c, "sed -n '1,80p' /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py"))
        parts.append(run(c, "sed -n '160,240p' /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py"))
        parts.append("=== MLS ensure_initial ===")
        parts.append(
            run(
                c,
                "grep -n 'audience_id\\|ensure_enterprise_gallery\\|ensure_initial_enterprise' "
                "/srv/contentswarm/backend/package/yuxi/services/material_library_service.py | head -n 80",
            )
        )
        parts.append("=== ContentMaterialCategory fields ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "import re\n"
                "p='/srv/contentswarm/backend/package/yuxi/models/content_material.py'\n"
                "import os\n"
                "for root,dirs,files in os.walk('/srv/contentswarm/backend/package/yuxi'):\n"
                "  for f in files:\n"
                "    if f.endswith('.py'):\n"
                "      path=os.path.join(root,f)\n"
                "      txt=open(path,encoding='utf-8',errors='ignore').read()\n"
                "      if 'class ContentMaterialCategory' in txt:\n"
                "        print('FILE', path)\n"
                "        i=txt.index('class ContentMaterialCategory')\n"
                "        print(txt[i:i+1800])\n"
                "PY",
            )
        )
        text = "\n".join(parts)
        open(OUT, "w", encoding="utf-8").write(text)
        print(text.encode("ascii", "replace").decode("ascii")[:4000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
