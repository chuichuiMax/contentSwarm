import os
from pathlib import Path
import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD") or Path(__file__).resolve().parents[2].joinpath(".deploy/10315.password").read_text().strip()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)
for cmd in [
    "docker top graph --sort=-pcpu 2>/dev/null | head -6",
    "curl -sS -o /dev/null -w 'editor_8080_sec=%{time_total} code=%{http_code}\\n' --max-time 90 http://127.0.0.1:8080/editor",
    "curl -sS -o /dev/null -w 'hycanvas_next_sec=%{time_total}\\n' --max-time 90 http://127.0.0.1:3000/editor",
    "du -sh /srv/contentswarm/docker/volumes/* 2>/dev/null | sort -hr | head -10",
    "docker exec graph cypher-shell -u neo4j -p test 'RETURN 1' 2>&1 | head -3 || true",
]:
    print("===", cmd.split("\\n")[0][:70], "===")
    _, o, e = c.exec_command(cmd, timeout=120)
    print((o.read() + e.read()).decode("utf-8", "replace"))
c.close()
