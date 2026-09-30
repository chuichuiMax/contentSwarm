import sys
import paramiko

HOST, USER, PASSWORD = "172.16.103.15", "root", "Hirun2026@"
ADMIN_UID = "tiechuideUId"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=30)


def run(cmd, timeout=900):
    print(f"$ {cmd[:200]}...", flush=True)
    _i, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    text = (out + err).strip()
    if text:
        print(text[-8000:], flush=True)
    print(f"exit={code}\n", flush=True)
    return code, text


run("docker ps --format '{{.Names}}' | grep -E 'api-dev|worker-dev'")
run(
    "docker exec postgres psql -U postgres -d yuxi -tAc "
    "\"SELECT uid, role FROM users WHERE uid='tiechuideUId' OR role IN ('admin','superadmin') LIMIT 15;\" 2>/dev/null "
    "|| docker compose -f /srv/contentswarm/docker-compose.yml exec -T postgres sh -c "
    "'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -tAc \"SELECT uid, role FROM users WHERE uid='\\''tiechuideUId'\\'' OR role IN ('\\''admin'\\'','\\''superadmin'\\'') LIMIT 15;\"'",
    timeout=60,
)

c.close()
