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

release_manifest_value() {
  local key=$1
  local manifest=$2
  sed -n "s/^${key}=//p" "$manifest"
}

validate_release() {
  local release_sha=$1
  local directory="$release_root/$release_sha"
  [[ "$release_sha" =~ ^[0-9a-f]{40,64}$ ]] || return 1
  [[ -f "$directory/manifest.env" ]] || return 1
  [[ -f "$directory/web-dist/index.html" ]] || return 1
  [[ -x "$directory/hycanvas" ]] || return 1
  [[ "$(release_manifest_value GIT_SHA "$directory/manifest.env")" == "$release_sha" ]]
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
      return 0
    fi
    if ((attempt < health_attempts)); then
      sleep "$health_interval_seconds"
    fi
  done
  return 1
}

recreate_business_services() {
  compose up -d --force-recreate \
    sandbox-provisioner xhs-browser-gateway hycanvas-db hycanvas-app hycanvas-init api worker web
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

validate_only=false
no_pull=false
for argument in "$@"; do
  case "$argument" in
    --validate-only) validate_only=true ;;
    --no-pull) no_pull=true ;;
    *) fail "未知参数: $argument" ;;
  esac
done

repo=${SOURCE_DEPLOY_REPO:-$(git rev-parse --show-toplevel 2>/dev/null || true)}
release_root=${SOURCE_DEPLOY_RELEASE_ROOT:-}
env_file=${SOURCE_DEPLOY_ENV_FILE:-}
compose_file=${SOURCE_DEPLOY_COMPOSE_FILE:-$repo/docker-compose.source.yml}
deploy_branch=${SOURCE_DEPLOY_BRANCH:-main}
state_root=${SOURCE_DEPLOY_STATE_ROOT:-$repo/.deploy/source-deploy}
health_attempts=${HEALTH_ATTEMPTS:-30}
health_interval_seconds=${HEALTH_INTERVAL_SECONDS:-5}
api_health_url=${SOURCE_DEPLOY_API_HEALTH_URL:-http://127.0.0.1:${WEB_HOST_PORT:-8090}/api/system/health}
web_health_url=${SOURCE_DEPLOY_WEB_HEALTH_URL:-http://127.0.0.1:${WEB_HOST_PORT:-8090}/}
hycanvas_health_url=${SOURCE_DEPLOY_HYCANVAS_HEALTH_URL:-http://127.0.0.1:${HYCANVAS_PORT:-8005}/healthz}

for command_name in git docker curl sed readlink; do
  command -v "$command_name" >/dev/null || fail "缺少部署命令: $command_name"
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
[[ "$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" == "$repo" ]] || fail "SOURCE_DEPLOY_REPO 必须是 Git 根目录"
git check-ref-format --branch "$deploy_branch" >/dev/null || fail "部署分支名称非法: $deploy_branch"
git -C "$repo" show-ref --verify --quiet "refs/heads/$deploy_branch" || fail "本地部署分支不存在: $deploy_branch"
[[ -z "$(git -C "$repo" status --porcelain --untracked-files=no)" ]] || fail "工作区存在未提交的已跟踪文件"
[[ "$health_attempts" =~ ^[1-9][0-9]*$ ]] || fail "HEALTH_ATTEMPTS 必须是正整数"
[[ "$health_interval_seconds" =~ ^[0-9]+$ ]] || fail "HEALTH_INTERVAL_SECONDS 必须是非负整数"

compose config --quiet
while IFS= read -r image; do
  [[ -n "$image" ]] || continue
  [[ "$image" != *:latest ]] || fail "基础镜像不得使用 latest: $image"
  docker image inspect "$image" >/dev/null || fail "基础镜像不存在: $image"
done < <(compose --profile release config --images | LC_ALL=C sort -u)

if $validate_only; then
  echo "源码部署预检通过"
  exit 0
fi

old_git_sha=$(git -C "$repo" rev-parse HEAD)
old_branch=$(git -C "$repo" symbolic-ref --short -q HEAD || true)
old_release_sha=$(current_release)
state_changed=0

rollback_after_failure() {
  local original_status=$?
  trap - ERR
  if [[ "$state_changed" == 1 ]]; then
    echo "部署失败，开始恢复 Git 与发布指针" >&2
    set +e
    git -C "$repo" switch --detach "$old_git_sha" >/dev/null
    git_status=$?
    release_status=0
    if [[ -n "$old_release_sha" ]]; then
      failed_release_sha=$(current_release)
      switch_release "$old_release_sha" "$failed_release_sha"
      release_status=$?
    else
      release_status=1
      echo "部署前不存在可恢复的发布产物" >&2
    fi
    recreate_business_services
    recreate_status=$?
    wait_for_health
    health_status=$?
    set -e
    if [[ "$git_status" == 0 && "$release_status" == 0 && "$recreate_status" == 0 && "$health_status" == 0 ]]; then
      echo "部署失败，已回滚到 $old_git_sha" >&2
    else
      echo "部署失败且自动回滚不完整，请立即人工处理" >&2
    fi
  fi
  exit "$original_status"
}
trap rollback_after_failure ERR

state_changed=1
compose stop api worker xhs-browser-gateway sandbox-provisioner
git -C "$repo" switch "$deploy_branch" >/dev/null
if ! $no_pull; then
  git -C "$repo" pull --ff-only
fi
new_git_sha=$(git -C "$repo" rev-parse HEAD)

compose run --rm --no-deps api bash /opt/source-deploy/check-runtime-lock.sh verify \
  /opt/runtime-locks/api.sha256 \
  /opt/source-locks/backend.uv.lock \
  /opt/source-locks/package.uv.lock
compose --profile release run --rm release-builder
new_release_sha=$(current_release)
if [[ "$new_release_sha" != "$new_git_sha" ]]; then
  echo "发布指针与 Git SHA 不一致: $new_release_sha != $new_git_sha" >&2
  false
fi
if ! validate_release "$new_release_sha"; then
  echo "新发布产物不完整: $new_release_sha" >&2
  false
fi

recreate_business_services
if ! wait_for_health; then
  echo "新版本健康检查失败" >&2
  false
fi
write_state "$new_git_sha" "$old_git_sha" "$new_release_sha" "$old_release_sha"

state_changed=0
trap - ERR
echo "源码部署成功: $new_git_sha（部署前分支: ${old_branch:-DETACHED}）"
