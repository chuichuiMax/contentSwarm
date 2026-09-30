import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password="Hirun2026@", timeout=30)

cmds = [
    "docker exec api-dev printenv POSTGRES_USER POSTGRES_DB",
    "docker exec api-dev uv run python /app/scripts/check_ct06_remote.py",
    "docker logs worker-dev --tail 35 2>&1",
]

# upload checker
checker = """
from yuxi.content.v3.foreman_rules import load_foreman_rule_catalog, upgrade_craft_daily_rules
from yuxi.storage.postgres.manager import pg_manager
from yuxi.repositories.content_repository import ContentRepository
import asyncio

async def main():
    g = next(x for x in load_foreman_rule_catalog()["combination_rules"] if x["content_type_codes"] == ["CT06"])
    print("catalog CT06 titles:", g["title_formula_candidate_codes"])
    pg_manager.initialize()
    async with pg_manager.AsyncSession() as db:
        repo = ContentRepository(db)
        current = await repo.get_published_rule_version(schema_version=3)
        bundle = await repo.get_rule_bundle(current.id, include_disabled=True)
        print("published:", current.id, "v", current.version)
        titles = {x["code"] for x in bundle["title_formulas"]}
        print("published has FRT23:", "FRT23" in titles, "FRT16:", "FRT16" in titles)
        ct6 = next(x for x in bundle["combination_rules"] if x.get("content_type_codes") == ["CT06"])
        print("published CT06 title candidates:", ct6["title_formula_candidate_codes"])
        upgraded = upgrade_craft_daily_rules(bundle)
        print("upgrade changed:", upgraded != bundle)
    await pg_manager.async_engine.dispose()

asyncio.run(main())
"""
sftp = c.open_sftp()
with sftp.file("/tmp/check_ct06_remote.py", "w") as f:
    f.write(checker)
sftp.close()

for cmd in cmds[:-1] + ["docker cp /tmp/check_ct06_remote.py api-dev:/app/scripts/check_ct06_remote.py"] + [cmds[1], cmds[2]]:
    print("===", cmd, "===", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=300)
    print((o.read() + e.read()).decode("utf-8", "replace")[:12000], flush=True)

run_sql = """
USER=$(docker exec api-dev printenv POSTGRES_USER)
DB=$(docker exec api-dev printenv POSTGRES_DB)
docker exec postgres psql -U "$USER" -d "$DB" -c "SELECT status, left(coalesce(error_message,''),120) err, count(*) FROM content_viral_article_versions GROUP BY 1,2 ORDER BY 3 DESC LIMIT 12;"
"""
print("=== sql ===", flush=True)
_i, o, e = c.exec_command(run_sql, timeout=120)
print((o.read() + e.read()).decode("utf-8", "replace"), flush=True)
c.close()
