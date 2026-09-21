#!/usr/bin/env bash
set -Eeuo pipefail

fail() {
  echo "$*" >&2
  exit 1
}

require_absolute_path() {
  local name=$1
  local value=$2
  [[ -n "$value" && "$value" == /* ]] || fail "$name 必须是非空绝对路径: $value"
  [[ "$value" != "/" ]] || fail "$name 不得指向根目录"
  [[ -z "${HOME:-}" || "$value" != "$HOME" ]] || fail "$name 不得指向 HOME"
  [[ "$value" != *'$'* ]] || fail "$name 包含未解析变量: $value"
}

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

state_value() {
  local key=$1
  local state_file=$2
  sed -n "s/^${key}=//p" "$state_file"
}

release_manifest_value() {
  local key=$1
  local manifest=$2
  sed -n "s/^${key}=//p" "$manifest"
}

tree_digest() {
  local directory=$1
  (
    cd "$directory"
    while IFS= read -r -d '' file; do
      sha256sum "$file"
    done < <(find . -type f -print0 | LC_ALL=C sort -z)
  ) | sha256sum | awk '{print $1}'
}

validate_release() {
  local release_sha=$1
  local directory="$release_root/$release_sha"
  local manifest="$directory/manifest.env"
  [[ "$release_sha" =~ ^[0-9a-f]{40,64}$ ]] || return 1
  [[ -f "$manifest" ]] || return 1
  [[ -f "$directory/web-dist/index.html" ]] || return 1
  [[ -x "$directory/hycanvas" ]] || return 1
  [[ "$(release_manifest_value FORMAT_VERSION "$manifest")" == "1" ]] || return 1
  [[ "$(release_manifest_value GIT_SHA "$manifest")" == "$release_sha" ]] || return 1
  [[ "$(release_manifest_value WEB_SHA256 "$manifest")" == "$(tree_digest "$directory/web-dist")" ]] \
    || return 1
  [[ "$(release_manifest_value HYCANVAS_SHA256 "$manifest")" == "$(sha256sum "$directory/hycanvas" | awk '{print $1}')" ]]
}

current_release() {
  if [[ -L "$release_root/current" ]]; then
    readlink "$release_root/current"
  fi
}

switch_release() {
  local target_sha=$1
  local previous_sha=${2:-}
  local current_next="$release_root/current.next"
  local previous_next="$release_root/previous.next"

  validate_release "$target_sha" || fail "发布产物不存在或不完整: $target_sha"
  [[ ! -e "$release_root/current" || -L "$release_root/current" ]] || fail "current 必须是符号链接"
  [[ ! -e "$release_root/previous" || -L "$release_root/previous" ]] || fail "previous 必须是符号链接"

  rm -f -- "$current_next" "$previous_next"
  if [[ -n "$previous_sha" && "$previous_sha" != "$target_sha" ]]; then
    validate_release "$previous_sha" || fail "上一发布产物不存在或不完整: $previous_sha"
    ln -s "$previous_sha" "$previous_next"
    mv -Tf -- "$previous_next" "$release_root/previous"
  fi
  ln -s "$target_sha" "$current_next"
  mv -Tf -- "$current_next" "$release_root/current"
}

wait_for_health() {
  local attempt
  for ((attempt = 1; attempt <= health_attempts; attempt++)); do
    if curl -fsS "$api_health_url" >/dev/null \
      && compose exec -T xhs-browser-gateway curl -fsS http://127.0.0.1:5051/health >/dev/null \
      && compose exec -T worker sh -ec 'kill -0 1' >/dev/null \
      && curl -fsS "$web_health_url" >/dev/null \
      && curl -fsS "$hycanvas_health_url" >/dev/null; then
      echo "健康检查通过: API Gateway Worker Web HyCanvas"
      return 0
    fi
    if ((attempt < health_attempts)); then
      sleep "$health_interval_seconds"
    fi
  done
  return 1
}

recreate_business_services() {
  compose up -d --no-recreate postgres redis minio etcd milvus graph hycanvas-db
  compose up -d --wait --no-deps --force-recreate \
    sandbox-provisioner xhs-browser-gateway hycanvas-app
  compose run --rm --no-deps hycanvas-init
  compose up -d --no-deps --force-recreate api worker web
}

write_state() {
  local git_sha=$1
  local previous_git_sha=$2
  local release_sha=$3
  local previous_release_sha=$4
  local state_tmp="$state_root/current-state.env.tmp"
  local history_tmp="$state_root/releases/$git_sha.env.tmp"

  mkdir -p "$state_root/releases"
  cat > "$state_tmp" <<EOF
GIT_SHA=$git_sha
PREVIOUS_GIT_SHA=$previous_git_sha
BRANCH=$deploy_branch
RELEASE_SHA=$release_sha
PREVIOUS_RELEASE_SHA=$previous_release_sha
DEPLOYED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  cp "$state_tmp" "$history_tmp"
  mv -f -- "$history_tmp" "$state_root/releases/$git_sha.env"
  mv -f -- "$state_tmp" "$state_root/current-state.env"
}

[[ $# -le 1 ]] || fail "用法: rollback-source.sh [git-sha]"

repo=${SOURCE_DEPLOY_REPO:-$(git rev-parse --show-toplevel 2>/dev/null || true)}
release_root=${SOURCE_DEPLOY_RELEASE_ROOT:-}
env_file=${SOURCE_DEPLOY_ENV_FILE:-}
compose_file=${SOURCE_DEPLOY_COMPOSE_FILE:-$repo/docker-compose.source.yml}
deploy_branch=${SOURCE_DEPLOY_BRANCH:-main}
state_root=${SOURCE_DEPLOY_STATE_ROOT:-$repo/.deploy/source-deploy}
state_file="$state_root/current-state.env"
health_attempts=${HEALTH_ATTEMPTS:-30}
health_interval_seconds=${HEALTH_INTERVAL_SECONDS:-5}
api_health_url=${SOURCE_DEPLOY_API_HEALTH_URL:-http://127.0.0.1:${WEB_HOST_PORT:-8090}/api/system/health}
web_health_url=${SOURCE_DEPLOY_WEB_HEALTH_URL:-http://127.0.0.1:${WEB_HOST_PORT:-8090}/}
hycanvas_health_url=${SOURCE_DEPLOY_HYCANVAS_HEALTH_URL:-http://127.0.0.1:${HYCANVAS_PORT:-8005}/healthz}

for command_name in git docker curl sed readlink sha256sum find sort awk; do
  command -v "$command_name" >/dev/null || fail "缺少回滚命令: $command_name"
done

require_absolute_path SOURCE_DEPLOY_REPO "$repo"
require_absolute_path SOURCE_DEPLOY_RELEASE_ROOT "$release_root"
require_absolute_path SOURCE_DEPLOY_ENV_FILE "$env_file"
require_absolute_path SOURCE_DEPLOY_COMPOSE_FILE "$compose_file"
require_absolute_path SOURCE_DEPLOY_STATE_ROOT "$state_root"
[[ -d "$repo" ]] || fail "Git 工作区不存在: $repo"
[[ -d "$release_root" ]] || fail "发布目录不存在: $release_root"
[[ -f "$env_file" ]] || fail "部署环境文件不存在: $env_file"
[[ -f "$compose_file" ]] || fail "Compose 文件不存在: $compose_file"
[[ -f "$state_file" ]] || fail "部署状态文件不存在: $state_file"
[[ "$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" == "$repo" ]] || fail "SOURCE_DEPLOY_REPO 必须是 Git 根目录"
[[ -z "$(git -C "$repo" status --porcelain --untracked-files=no)" ]] || fail "工作区存在未提交的已跟踪文件"
[[ "$health_attempts" =~ ^[1-9][0-9]*$ ]] || fail "HEALTH_ATTEMPTS 必须是正整数"
[[ "$health_interval_seconds" =~ ^[0-9]+$ ]] || fail "HEALTH_INTERVAL_SECONDS 必须是非负整数"

target_sha=${1:-$(state_value PREVIOUS_GIT_SHA "$state_file")}
[[ -n "$target_sha" ]] || fail "没有可回滚的上一 Git SHA"
target_sha=$(git -C "$repo" rev-parse "${target_sha}^{commit}" 2>/dev/null) || fail "未知 Git SHA: ${1:-}"
validate_release "$target_sha" || fail "目标 SHA 缺少完整发布产物: $target_sha"

compose config --quiet
while IFS= read -r image; do
  [[ -n "$image" ]] || continue
  [[ "$image" != *:latest ]] || fail "基础镜像不得使用 latest: $image"
  docker image inspect "$image" >/dev/null || fail "基础镜像不存在: $image"
done < <(compose --profile release config --images | LC_ALL=C sort -u)

old_git_sha=$(git -C "$repo" rev-parse HEAD)
old_release_sha=$(current_release)
[[ -n "$old_release_sha" ]] || fail "当前发布指针不存在，无法安全回滚"
validate_release "$old_release_sha" || fail "当前发布产物不完整: $old_release_sha"

compose stop api worker xhs-browser-gateway sandbox-provisioner
git -C "$repo" switch --detach "$target_sha" >/dev/null
switch_release "$target_sha" "$old_release_sha"
recreate_business_services

if ! wait_for_health; then
  echo "目标版本健康检查失败，恢复回滚前版本" >&2
  git -C "$repo" switch --detach "$old_git_sha" >/dev/null
  switch_release "$old_release_sha" "$target_sha"
  recreate_business_services
  wait_for_health || fail "回滚目标与原版本均不健康，请立即人工处理"
  fail "回滚目标健康检查失败，已恢复原版本"
fi

write_state "$target_sha" "$old_git_sha" "$target_sha" "$old_release_sha"
target_manifest="$release_root/$target_sha/manifest.env"
echo "发布产物摘要: GIT_SHA=$target_sha WEB_SHA256=$(release_manifest_value WEB_SHA256 "$target_manifest") HYCANVAS_SHA256=$(release_manifest_value HYCANVAS_SHA256 "$target_manifest")"
echo "容器状态:"
compose ps
echo "显式回滚成功: $target_sha"
