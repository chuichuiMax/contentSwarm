#!/usr/bin/env bash
set -euo pipefail

# Export only content rule/configuration tables. Runtime tasks, users and run
# history are intentionally excluded from the Git-tracked artifact.
output_file="${1:-backend/data/content-rules.sql}"
mkdir -p "$(dirname "$output_file")"

tables=(
  content_rule_versions
  content_creation_methods
  content_title_formulas
  content_body_formulas
  content_combination_rules
  content_workflow_versions
  content_industry_template_versions
  content_type_definitions
  content_formula_patterns
  content_formula_slot_bindings
  content_lexicon_packs
  content_lexicon_versions
  content_lexicon_entries
  content_variable_definitions
  content_industry_pack_versions
  content_industry_variable_mappings
  content_persona_profiles
  content_persona_profile_versions
  content_channel_profiles
  content_channel_profile_versions
  content_compliance_policy_versions
  content_replacement_rules
)

table_args=()
for table in "${tables[@]}"; do
  table_args+=(--table "$table")
done

docker compose exec -T postgres pg_dump \
  --data-only --inserts --column-inserts --no-owner --no-privileges \
  -U "${POSTGRES_USER:-postgres}" "${table_args[@]}" "${POSTGRES_DB:-yuxi}" > "$output_file"

echo "Exported content rules to $output_file"
