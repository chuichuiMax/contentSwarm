#!/usr/bin/env bash
set -Eeuo pipefail

# 当家测试服务器首次部署时导入最新内容规则快照。
# 该脚本与日常发布入口分离，只允许初始化没有内容任务、没有人工规则版本的数据库。

fail() {
  echo "错误: $*" >&2
  exit 1
}

repo=${CONTENTSWARM_TEST_REPO:-/dangjia/project/contentswarm/repo}
env_file=${CONTENTSWARM_TEST_ENV_FILE:-$repo/.env.test}
compose_file=${CONTENTSWARM_TEST_COMPOSE_FILE:-$repo/docker-compose-test.yml}
snapshot=${CONTENTSWARM_CONTENT_RULES_SNAPSHOT:-$repo/backend/data/content-rules.sql}
import_script=${CONTENTSWARM_CONTENT_RULES_IMPORT_SCRIPT:-$repo/backend/scripts/import_content_rules.sh}
backup_root=${CONTENTSWARM_TEST_BACKUP_ROOT:-/dangjia/project/contentswarm/backups}

[[ -f "$env_file" ]] || fail "缺少环境文件: $env_file"
[[ -f "$compose_file" ]] || fail "缺少 Compose 文件: $compose_file"
[[ -f "$snapshot" ]] || fail "缺少内容规则快照: $snapshot"
[[ -f "$import_script" ]] || fail "缺少内容规则导入脚本: $import_script"

target_version=$(
  sed -nE "s/.*content-rules-platform-v([0-9]+).*'published'.*/\1/p" "$snapshot" | tail -n 1
)
[[ "$target_version" =~ ^[0-9]+$ ]] || fail "无法从快照识别已发布规则版本"

compose=(docker compose --env-file "$env_file" -f "$compose_file")
"${compose[@]}" config --quiet

database_scalar() {
  local sql=$1
  "${compose[@]}" exec -T postgres sh -lc \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' \
    sh "$sql" | tr -d '\r' | tail -n 1
}

current_version=$(database_scalar \
  "SELECT COALESCE(MAX(version), 0) FROM content_rule_versions WHERE tenant_id IS NULL AND status = 'published';")
[[ "$current_version" =~ ^[0-9]+$ ]] || fail "无法读取当前已发布规则版本"

if (( current_version >= target_version )); then
  echo "内容规则已是 v${current_version}，快照目标为 v${target_version}，无需初始化。"
  exit 0
fi

task_count=$(database_scalar "SELECT COUNT(*) FROM content_tasks;")
[[ "$task_count" =~ ^[0-9]+$ ]] || fail "无法读取内容任务数量"
(( task_count == 0 )) || fail "当前存在 ${task_count} 个内容任务，拒绝全量导入规则快照"

manual_version_count=$(database_scalar \
  "SELECT COUNT(*) FROM content_rule_versions WHERE created_by IS DISTINCT FROM 'system';")
[[ "$manual_version_count" =~ ^[0-9]+$ ]] || fail "无法读取人工规则版本数量"
(( manual_version_count == 0 )) || fail \
  "当前存在 ${manual_version_count} 个非系统规则版本，拒绝覆盖现有配置"

mkdir -p "$backup_root"
umask 077
backup_file="$backup_root/yuxi-before-content-rules-v${target_version}-$(date +%Y%m%d-%H%M%S).dump"
"${compose[@]}" exec -T postgres sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$backup_file"
[[ -s "$backup_file" ]] || fail "数据库备份为空: $backup_file"
echo "数据库备份: $backup_file"

services_stopped=false
restart_business_services() {
  if [[ "$services_stopped" == true ]]; then
    "${compose[@]}" up -d --no-deps api worker >/dev/null || true
  fi
}
trap restart_business_services EXIT

"${compose[@]}" stop api worker
services_stopped=true

(
  cd "$repo"
  COMPOSE_FILE="$compose_file" \
  COMPOSE_ENV_FILES="$env_file" \
    bash "$import_script" "$snapshot"
)

imported_version=$(database_scalar \
  "SELECT COALESCE(MAX(version), 0) FROM content_rule_versions WHERE tenant_id IS NULL AND status = 'published';")
[[ "$imported_version" == "$target_version" ]] || fail \
  "快照导入后发布版本为 v${imported_version}，预期为 v${target_version}"

"${compose[@]}" up -d --wait --no-deps api worker
services_stopped=false
trap - EXIT

echo "内容规则首次初始化完成: v${current_version} -> v${imported_version}"
