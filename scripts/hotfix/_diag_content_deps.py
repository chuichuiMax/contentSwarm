#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=90):
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
        script = r'''
python3 - <<'PY'
from pathlib import Path
files = [
'/srv/contentswarm/web/src/components/material/CaseGalleryStyleFilter.vue',
'/srv/contentswarm/web/src/components/content/ContentPhotoComposition.vue',
'/srv/contentswarm/web/src/data/contentTestCases.js',
'/srv/contentswarm/web/src/data/contentTestCases.ts',
'/srv/contentswarm/web/src/utils/contentRequestPayload.js',
]
for f in files:
    p=Path(f)
    print(('OK' if p.exists() else 'MISS'), f, p.stat().st_size if p.exists() else '-')
# check onMounted throw conditions in ContentStudioView
txt=Path('/srv/contentswarm/web/src/views/ContentStudioView.vue').read_text(encoding='utf-8')
idx=txt.find('onMounted')
print('onMounted_at', idx)
print(txt[idx:idx+2200])
PY
'''
        parts = [run(c, script)]
        parts.append("=== curl deps ===")
        for path in [
            "/src/components/material/CaseGalleryStyleFilter.vue",
            "/src/components/content/ContentPhotoComposition.vue",
            "/src/data/contentTestCases.js",
            "/src/utils/contentRequestPayload.js",
        ]:
            parts.append(
                run(
                    c,
                    f"curl -s -m 15 -o /dev/null -w '{path} %{{http_code}} %{{size_download}}\\n' "
                    f"'http://127.0.0.1:5173{path}'",
                )
            )
        parts.append("=== api content bootstrap endpoints ===")
        parts.append(
            run(
                c,
                "curl -s -m 8 -o /dev/null -w 'health:%{http_code}\\n' http://127.0.0.1:5050/api/system/health; "
                "curl -s -m 8 -o /dev/null -w 'templates:%{http_code}\\n' http://127.0.0.1:5050/api/content/industry-templates || true"
            )
        )
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:6000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
