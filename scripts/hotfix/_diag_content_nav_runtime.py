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
        parts = []
        parts.append("=== 8080 content/new ===")
        parts.append(
            run(
                c,
                "curl -s -m 10 -o /tmp/cnew.html -w 'code:%{http_code} size:%{size_download} redir:%{redirect_url}\\n' "
                "http://127.0.0.1:8080/content/new; "
                "python3 -c \"print(open('/tmp/cnew.html',encoding='utf-8',errors='replace').read()[:500])\""
            )
        )
        parts.append("=== nginx / vite proxy config snippet ===")
        parts.append(
            run(
                c,
                "ls /srv/contentswarm/docker /etc/nginx/conf.d 2>/dev/null; "
                "grep -RIn --include='*.conf' --include='*.yml' -e 'content' -e 'location' "
                "/srv/contentswarm/docker /srv/contentswarm/nginx 2>/dev/null | head -n 40; "
                "grep -n 'proxy\\|5173\\|8080' /srv/contentswarm/docker-compose.yml 2>/dev/null | head -n 40"
            )
        )
        # Diff local/remote ContentStudio signature hash
        parts.append("=== remote ContentStudio hash / broken imports scan ===")
        parts.append(
            run(
                c,
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "import hashlib,re\n"
                "p=Path('/srv/contentswarm/web/src/views/ContentStudioView.vue')\n"
                "data=p.read_bytes()\n"
                "print('sha', hashlib.sha256(data).hexdigest()[:16], 'size', len(data))\n"
                "txt=data.decode('utf-8','replace')\n"
                "# find imports that might 404\n"
                "imps=re.findall(r\\\"from '(@/[^']+)'\\\", txt)\n"
                "print('import_count', len(imps))\n"
                "root=Path('/srv/contentswarm/web/src')\n"
                "missing=[]\n"
                "for imp in imps:\n"
                "  rel=imp[2:]\n"
                "  candidates=[root/rel, root/(rel+'.js'), root/(rel+'.vue'), root/(rel+'.ts')]\n"
                "  if not any(c.exists() for c in candidates):\n"
                "    # also index files\n"
                "    if not (root/rel/'index.js').exists() and not (root/rel/'index.ts').exists():\n"
                "      missing.append(imp)\n"
                "print('missing', missing[:30])\n"
                "PY"
            )
        )
        parts.append("=== web-dev restart health ===")
        parts.append(run(c, "docker inspect -f '{{.State.Status}} {{.State.Health.Status}} {{.RestartCount}}' web-dev api-dev"))
        parts.append(run(c, "docker logs web-dev --tail 30 2>&1 | tail -n 30"))
        text = "\n".join(parts)
        OUT.write_text(text, encoding="utf-8")
        print(text.encode("ascii", "replace").decode("ascii")[:6000])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
