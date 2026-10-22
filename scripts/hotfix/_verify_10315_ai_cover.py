import os
import sys
import time

import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit("DEPLOY_SSH_PASSWORD not set")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

for i in range(18):
    _, o, _ = c.exec_command(
        "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5050/openapi.json",
        timeout=15,
    )
    if o.read().decode().strip() == "200":
        print(f"api ready (~{i * 5}s)")
        break
    time.sleep(5)
else:
    print("api not ready", file=sys.stderr)
    sys.exit(2)

cmd = (
    "docker exec api-dev python -c "
    "\"from yuxi.content_cover.ai_cover_prompt_catalog import specs_for_content_type; "
    "a=[s.variant for s in specs_for_content_type(content_type_code='CT02')]; "
    "b=[s.variant for s in specs_for_content_type(content_type_name='\\u88c5\\u4fee\\u6848\\u4f8b\\u5206\\u4eab')]; "
    "print('CT02', len(a), 'p12' in a); print('case', len(b), 'p12' in b)\""
)
_, o, e = c.exec_command(cmd, timeout=180)
print(o.read().decode(), end="")
err = e.read().decode().strip()
if err:
    print(err, file=sys.stderr)
    sys.exit(3)
_, o2, _ = c.exec_command("docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-dev|web-dev'", timeout=15)
print(o2.read().decode(), end="")
c.close()
