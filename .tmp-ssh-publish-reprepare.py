import sys
import paramiko

HOST, USER, PASSWORD = "172.16.103.15", "root", "Hirun2026@"
ADMIN_UID = "tiechuideUId"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=30)


def run(cmd, timeout=900):
    print(f"$ {cmd}", flush=True)
    _i, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    text = (out + err).strip()
    print(text[-12000:] if len(text) > 12000 else text, flush=True)
    print(f"exit={code}\n", flush=True)
    return code


code = run(
    f"docker exec api-dev uv run python scripts/publish_craft_daily_rules.py --uid {ADMIN_UID} --publish",
    timeout=600,
)
if code != 0:
    sys.exit(code)

run(
    "docker exec api-dev uv run python -c \""
    "import asyncio; from sqlalchemy import select; "
    "from yuxi.storage.postgres.manager import pg_manager; "
    "from yuxi.storage.postgres.models_knowledge import KnowledgeBase; "
    "async def q(): "
    " pg_manager.initialize(); "
    " async with pg_manager.AsyncSession() as db: "
    "  for row in (await db.execute(select(KnowledgeBase.kb_id, KnowledgeBase.name).where("
    "KnowledgeBase.additional_params['viral_content_type'].as_string()=='CT06'))).all(): "
    "   print(row); "
    " await pg_manager.async_engine.dispose(); "
    "asyncio.run(q())\"",
    timeout=180,
)

code = run(
    "docker exec api-dev uv run python scripts/reprepare_viral_types.py --apply",
    timeout=7200,
)
c.close()
sys.exit(code)
