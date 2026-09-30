import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password="Hirun2026@", timeout=30)


def run(cmd, timeout=120):
    print(f"$ {cmd}\n", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    t = (o.read().decode("utf-8", "replace") + e.read().decode("utf-8", "replace")).strip()
    print(t[-15000], flush=True)
    print("exit", o.channel.recv_exit_status(), "\n", flush=True)


run(
    "docker exec postgres psql -U postgres -d yuxi -c "
    "\"SELECT id, version, status, left(changelog,80) FROM content_rule_versions "
    "ORDER BY version DESC LIMIT 5;\""
)
run(
    "docker exec postgres psql -U postgres -d yuxi -tAc "
    "\"SELECT code FROM content_title_formulas WHERE version_id=(SELECT id FROM content_rule_versions WHERE status='published' LIMIT 1) AND code IN ('FRT16','FRT23') ORDER BY 1;\""
)
run(
    "docker exec postgres psql -U postgres -d yuxi -c "
    "\"SELECT title_formula_candidate_codes FROM content_combination_rules "
    "WHERE version_id=(SELECT id FROM content_rule_versions WHERE status='published' LIMIT 1) "
    "AND content_type_codes @> ARRAY['CT06']::varchar[] LIMIT 1;\""
)
run(
    "docker exec postgres psql -U postgres -d yuxi -c "
    "\"SELECT status, left(error_message,120) AS err, count(*) FROM content_viral_article_versions "
    "GROUP BY 1,2 ORDER BY count(*) DESC LIMIT 15;\""
)
run("docker logs worker-dev --tail 80 2>&1")
run("docker logs api-dev --tail 40 2>&1")

c.close()
