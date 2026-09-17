#!/usr/bin/env bash
set -euo pipefail

input_file="${1:-backend/data/content-rules.sql}"
if [[ ! -f "$input_file" ]]; then
  echo "Rule dump not found: $input_file" >&2
  exit 1
fi

# The dump is generated with INSERT statements. Existing rows are cleared so
# the imported snapshot exactly matches the exported rule configuration.
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-yuxi}" <<'SQL'
BEGIN;
TRUNCATE TABLE
  content_replacement_rules,
  content_compliance_policy_versions,
  content_channel_profile_versions,
  content_channel_profiles,
  content_persona_profile_versions,
  content_persona_profiles,
  content_industry_variable_mappings,
  content_industry_pack_versions,
  content_variable_definitions,
  content_lexicon_entries,
  content_lexicon_versions,
  content_lexicon_packs,
  content_formula_slot_bindings,
  content_formula_patterns,
  content_type_definitions,
  content_industry_template_versions,
  content_workflow_versions,
  content_combination_rules,
  content_body_formulas,
  content_title_formulas,
  content_creation_methods,
  content_rule_versions
RESTART IDENTITY CASCADE;
COMMIT;
SQL

cat "$input_file" | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-yuxi}"
echo "Imported content rules from $input_file"
