#!/bin/bash
USER=$(docker exec api-dev printenv POSTGRES_USER)
DB=$(docker exec api-dev printenv POSTGRES_DB)
echo "POSTGRES $USER $DB"
docker exec api-dev uv run python -c 'from yuxi.content.v3.foreman_rules import load_foreman_rule_catalog; g=next(x for x in load_foreman_rule_catalog()["combination_rules"] if x["content_type_codes"]==["CT06"]); print("CT06 titles", g["title_formula_candidate_codes"])'
docker exec api-dev uv run python scripts/publish_craft_daily_rules.py --uid tiechuideUId 2>&1 | tail -15
docker exec postgres psql -U "$USER" -d "$DB" -c "SELECT id, version, status, left(changelog,100) FROM content_rule_versions ORDER BY version DESC LIMIT 5;"
docker exec postgres psql -U "$USER" -d "$DB" -tAc "SELECT code FROM content_title_formulas WHERE version_id=(SELECT id FROM content_rule_versions WHERE status='published' ORDER BY version DESC LIMIT 1) AND code IN ('FRT16','FRT23') ORDER BY 1;"
docker exec postgres psql -U "$USER" -d "$DB" -c "SELECT status, left(coalesce(error_message,''),120) err, count(*) FROM content_viral_article_versions GROUP BY 1,2 ORDER BY 3 DESC LIMIT 15;"
docker logs worker-dev --tail 40 2>&1
